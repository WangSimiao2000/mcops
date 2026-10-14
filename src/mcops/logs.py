"""日志页后端（M4）：枚举日志源、读取/过滤/分页、跨文件搜索、下载。

日志来源：
- game_dir/logs/：latest.log + 约 370 个 MC 原生轮转的 YYYY-MM-DD-N.log.gz
- game_dir/crash-reports/：crash-*.txt
- systemd --user journal：mcops-agent.service（agent 自己）、mc-server.service（MC stdout）

MC 日志行格式 `[HH:MM:SS] [Thread/LEVEL]: message`：
- 日期从文件名取；latest.log 用文件 mtime 当天的日期。
- 跨午夜时时间往回跳，遇到回跳把日期 +1 天（复用 logparse 的逻辑）。
- 多行异常堆栈的后续行没有前缀，归到上一条（continuation）。

所有读取都是只读的，observe 模式下也能用。路径经白名单正则 + resolve 后仍必须落在目标目录内。
"""

from __future__ import annotations

import asyncio
import gzip
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from datetime import time as dtime
from pathlib import Path
from typing import IO, Any

# ---- 常量 -----------------------------------------------------------------

TAIL_DEFAULT = 500
TAIL_MAX = 5000
REGEX_MAX_LEN = 200
MAX_LINE_LEN = 10_000  # 单行超过此长度时截断，防正则灾难性回溯
GZ_DECOMP_CAP = 200 * 1024 * 1024  # 单个 .gz 解压后上限 200MB
SEARCH_HITS_MAX = 500
SEARCH_DEFAULT_DAYS = 7
SEARCH_MAX_DAYS = 60
SEARCH_BUDGET_S = 20.0
JOURNAL_TIMEOUT_S = 15.0
JOURNAL_DEFAULT_LINES = 2000
LEVELS = ("INFO", "WARN", "ERROR")

# 游戏日志文件名白名单：latest.log 或 YYYY-MM-DD-N.log(.gz)
_GAME_LOG_RE = re.compile(r"^(?:latest\.log|\d{4}-\d{2}-\d{2}-\d+\.log(?:\.gz)?)$")
_DATED_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d+)\.log(?:\.gz)?$")
_CRASH_RE = re.compile(r"^crash-[0-9A-Za-z._-]+\.txt$")
# MC 行前缀：[HH:MM:SS] [Thread/LEVEL]:
_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] \[([^\]]+)/([A-Z]+)\]: (.*)$")
# journal short-iso 行前缀：2026-10-09T17:50:31+0800 host unit[pid]: message
_JOURNAL_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:?\d{2})?)\s+\S+\s+[^:]+:\s?(.*)$"
)

_JOURNAL_UNITS = {"agent": "mcops-agent.service", "server": "mc-server.service"}


class LogsError(Exception):
    """带 HTTP 状态码的日志错误（API 层转成 HTTPException）。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(slots=True)
class ParsedLine:
    n: int
    time: str | None  # ISO 时间，无法解析时为 None
    level: str | None
    thread: str | None
    text: str
    continuation: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "time": self.time,
            "level": self.level,
            "thread": self.thread,
            "text": self.text,
            "continuation": self.continuation,
        }


# ---- 源枚举 ---------------------------------------------------------------


def _logs_dir(game_dir: Path) -> Path:
    return game_dir / "logs"


def _crash_dir(game_dir: Path) -> Path:
    return game_dir / "crash-reports"


def _file_date(name: str, path: Path) -> date | None:
    m = _DATED_RE.match(name)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if name == "latest.log":
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).date()
        except OSError:
            return None
    return None


def _seq(name: str) -> int:
    m = _DATED_RE.match(name)
    return int(m.group(4)) if m else -1


def file_date_for(game_dir: Path, name: str) -> date:
    """游戏日志源的起始日期：带日期文件名取文件名，latest.log 取 mtime 当天，兜底今天。"""
    path = _logs_dir(game_dir) / name
    fd = _file_date(name, path)
    return fd if fd is not None else date.today()


def list_game_logs(game_dir: Path) -> list[dict[str, Any]]:
    """游戏日志文件：带日期、序号、大小、mtime，按时间倒序（latest 最前）。"""
    d = _logs_dir(game_dir)
    out: list[dict[str, Any]] = []
    if not d.is_dir():
        return out
    for p in d.iterdir():
        if not p.is_file() or not _GAME_LOG_RE.match(p.name):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        fd = _file_date(p.name, p)
        out.append(
            {
                "name": p.name,
                "date": fd.isoformat() if fd else None,
                "seq": _seq(p.name),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
                "gzip": p.name.endswith(".gz"),
                "latest": p.name == "latest.log",
            }
        )
    # latest 永远最前；其余按 (date, seq) 倒序。
    out.sort(key=lambda r: (r["latest"], r["date"] or "", r["seq"]), reverse=True)
    return out


def list_crash_reports(game_dir: Path) -> list[dict[str, Any]]:
    d = _crash_dir(game_dir)
    out: list[dict[str, Any]] = []
    if not d.is_dir():
        return out
    for p in d.iterdir():
        if not p.is_file() or not _CRASH_RE.match(p.name):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(
            {
                "name": p.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
            }
        )
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


async def list_sources(game_dir: Path) -> dict[str, Any]:
    game, crashes = await asyncio.to_thread(_list_sources_sync, game_dir)
    journal = await _journal_sources()
    return {"game": game, "crash": crashes, "journal": journal}


def _list_sources_sync(game_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return list_game_logs(game_dir), list_crash_reports(game_dir)


async def _journal_sources() -> list[dict[str, Any]]:
    """探测 journalctl 是否可用；不可用时标记 available=False 并给出友好的错误。"""
    available, error = await _journalctl_available()
    return [
        {
            "name": key,
            "unit": unit,
            "available": available,
            "error": error,
        }
        for key, unit in _JOURNAL_UNITS.items()
    ]


# ---- 路径安全 -------------------------------------------------------------


def resolve_game_log(game_dir: Path, name: str) -> Path:
    """校验游戏日志源名并返回安全的绝对路径。拒绝 ../、绝对路径、目录外符号链接。"""
    if not _GAME_LOG_RE.match(name):
        raise LogsError(400, "非法的日志文件名")
    return _safe_join(_logs_dir(game_dir), name)


def resolve_crash(game_dir: Path, name: str) -> Path:
    if not _CRASH_RE.match(name):
        raise LogsError(400, "非法的崩溃报告文件名")
    return _safe_join(_crash_dir(game_dir), name)


def _safe_join(base: Path, name: str) -> Path:
    # name 已过白名单（无 / 无 ..），但符号链接可能指向目录外，故 resolve 后再校验。
    base_r = base.resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base_r):
        raise LogsError(400, "路径越界")
    if not target.is_file():
        raise LogsError(404, "日志文件不存在")
    return target


# ---- 行解析 ---------------------------------------------------------------


class _Parser:
    """把原始行流解析成结构化行：推断日期（跨午夜 +1）、把无前缀行归为 continuation。"""

    def __init__(self, start: date, tz: tzinfo) -> None:
        self.day = start
        self.tz = tz
        self._last: dtime | None = None

    def feed(self, n: int, raw: str) -> ParsedLine:
        m = _LINE_RE.match(raw)
        if not m:
            return ParsedLine(n=n, time=None, level=None, thread=None, text=raw, continuation=True)
        hh, mm, ss, thread, level, msg = m.groups()
        t = dtime(int(hh), int(mm), int(ss))
        if (
            self._last is not None
            and t < self._last
            and (
                datetime.combine(date.min, self._last) - datetime.combine(date.min, t)
                > timedelta(hours=1)
            )
        ):
            self.day += timedelta(days=1)
        self._last = t
        ts = datetime.combine(self.day, t, tzinfo=self.tz)
        return ParsedLine(
            n=n,
            time=ts.isoformat(),
            level=level,
            thread=thread,
            text=msg,
            continuation=False,
        )


def _match_filters(
    line: ParsedLine, levels: set[str] | None, q: str | None, rx: re.Pattern[str] | None
) -> bool:
    """continuation 行跟随其归属的上一条，由调用方处理；此处只判断单行。"""
    if levels is not None and (line.level is None or line.level not in levels):
        return False
    text = line.text if len(line.text) <= MAX_LINE_LEN else line.text[:MAX_LINE_LEN]
    if rx is not None:
        if not rx.search(text):
            return False
    elif q is not None:
        if q.lower() not in text.lower():
            return False
    return True


def compile_regex(pattern: str) -> re.Pattern[str]:
    if len(pattern) > REGEX_MAX_LEN:
        raise LogsError(422, f"正则表达式过长（上限 {REGEX_MAX_LEN}）")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise LogsError(422, f"正则表达式编译失败：{e}") from None


# ---- 行读取（gz 流式）------------------------------------------------------


def _open_text(path: Path) -> IO[str]:
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def _iter_lines(path: Path) -> Any:
    """逐行产出已去尾换行的文本；.gz 流式解压并限制累计解压量。"""
    decompressed = 0
    with _open_text(path) as fh:
        for raw in fh:
            decompressed += len(raw)
            if decompressed > GZ_DECOMP_CAP:
                raise LogsError(413, "日志文件解压后超过 200MB 上限")
            yield raw.rstrip("\n").rstrip("\r")


def read_view(
    path: Path,
    file_date: date,
    tz: tzinfo,
    *,
    tail: int | None,
    offset: int | None,
    limit: int | None,
    levels: set[str] | None,
    q: str | None,
    rx: re.Pattern[str] | None,
) -> dict[str, Any]:
    """读取单个文件：先解析成结构化行，再按过滤 + tail/分页取窗口。

    continuation 行跟随其所属前一条的过滤结果一起保留，异常堆栈不会被级别过滤割裂。
    """
    parser = _Parser(file_date, tz)
    kept: list[ParsedLine] = []
    keep_prev = False  # 上一主行是否命中过滤，决定 continuation 是否保留
    n = 0
    for raw in _iter_lines(path):
        n += 1
        pl = parser.feed(n, raw)
        if pl.continuation:
            if keep_prev:
                kept.append(pl)
            continue
        if _match_filters(pl, levels, q, rx):
            kept.append(pl)
            keep_prev = True
        else:
            keep_prev = False

    total = len(kept)
    truncated = False
    if offset is not None or limit is not None:
        off = offset or 0
        lim = limit if limit is not None else TAIL_DEFAULT
        window = kept[off : off + lim]
        truncated = off + lim < total
    else:
        t = TAIL_DEFAULT if tail is None else max(1, min(tail, TAIL_MAX))
        window = kept[-t:]
        truncated = total > t
    return {
        "lines": [pl.to_json() for pl in window],
        "total": total,
        "truncated": truncated,
    }


# ---- 跨文件搜索 ------------------------------------------------------------


def search_files(
    game_dir: Path,
    tz: tzinfo,
    *,
    q: str,
    date_from: date,
    date_to: date,
    levels: set[str] | None,
    rx: re.Pattern[str] | None,
) -> dict[str, Any]:
    """按时间倒序逐文件扫描，最多 500 命中；超 20s 返回已找到的并标记 partial。阻塞，放 to_thread。"""
    deadline = time.monotonic() + SEARCH_BUDGET_S
    hits: list[dict[str, Any]] = []
    partial = False
    files = [
        r
        for r in list_game_logs(game_dir)
        if r["date"] is None or (date_from.isoformat() <= r["date"] <= date_to.isoformat())
    ]
    # list_game_logs 已按时间倒序（latest 最前）。
    for r in files:
        if len(hits) >= SEARCH_HITS_MAX:
            break
        if time.monotonic() > deadline:
            partial = True
            break
        fd = date.fromisoformat(r["date"]) if r["date"] else date.today()
        path = _logs_dir(game_dir) / r["name"]
        parser = _Parser(fd, tz)
        try:
            for raw in _iter_lines(path):
                if time.monotonic() > deadline:
                    partial = True
                    break
                pl = parser.feed(len(hits), raw)
                if pl.continuation:
                    continue
                if _match_filters(pl, levels, q, rx):
                    hits.append(
                        {
                            "source": r["name"],
                            "line": pl.n,
                            "time": pl.time,
                            "level": pl.level,
                            "text": pl.text,
                        }
                    )
                    if len(hits) >= SEARCH_HITS_MAX:
                        break
            else:
                continue
            break
        except LogsError:
            continue
    return {
        "hits": hits,
        "partial": partial,
        "scanned_from": date_from.isoformat(),
        "scanned_to": date_to.isoformat(),
    }


# ---- journal --------------------------------------------------------------


async def _journalctl_available() -> tuple[bool, str | None]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return False, "系统未安装 journalctl"
    except OSError as e:
        return False, f"无法调用 journalctl：{e}"
    try:
        await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except TimeoutError:
        with_kill(proc)
        return False, "journalctl 调用超时"
    return (proc.returncode == 0), (None if proc.returncode == 0 else "journalctl 返回错误")


def with_kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass


async def read_journal(key: str, tz: tzinfo, *, lines: int) -> dict[str, Any]:
    """用 journalctl --user -u <unit> -o short-iso 读取单元日志。"""
    unit = _JOURNAL_UNITS.get(key)
    if unit is None:
        raise LogsError(400, "未知的 journal 源")
    n = max(1, min(lines, TAIL_MAX))
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "--user",
            "-u",
            unit,
            "-o",
            "short-iso",
            "--no-pager",
            "-n",
            str(n),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise LogsError(503, "系统未安装 journalctl，无法读取系统日志") from None
    except OSError as e:
        raise LogsError(503, f"无法调用 journalctl：{e}") from None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=JOURNAL_TIMEOUT_S)
    except TimeoutError:
        with_kill(proc)
        raise LogsError(504, "读取系统日志超时") from None
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace").strip() or "journalctl 返回错误"
        raise LogsError(502, f"journalctl 失败：{msg}")
    text = stdout.decode("utf-8", errors="replace")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        out.append(_parse_journal_line(i, raw).to_json())
    return {"lines": out, "total": len(out), "truncated": False}


def _parse_journal_line(n: int, raw: str) -> ParsedLine:
    m = _JOURNAL_RE.match(raw)
    if not m:
        return ParsedLine(n=n, time=None, level=None, thread=None, text=raw, continuation=False)
    ts_raw, msg = m.groups()
    iso: str | None
    try:
        iso = _parse_iso(ts_raw).isoformat()
    except ValueError:
        iso = None
    level = _infer_level(msg)
    return ParsedLine(n=n, time=iso, level=level, thread=None, text=msg, continuation=False)


def _parse_iso(ts_raw: str) -> datetime:
    s = ts_raw
    # 规范化 +0800 -> +08:00，供 fromisoformat（3.12 虽支持无冒号偏移，但稳妥起见统一）
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":":
        s = s[:-2] + ":" + s[-2:]
    return datetime.fromisoformat(s)


def _infer_level(msg: str) -> str | None:
    low = msg.lower()
    if "error" in low or "traceback" in low or "exception" in low:
        return "ERROR"
    if "warn" in low:
        return "WARN"
    return "INFO"


# ---- 下载辅助 -------------------------------------------------------------


def download_media_type(path: Path) -> str:
    if path.name.endswith(".gz"):
        return "application/gzip"
    return "text/plain; charset=utf-8"
