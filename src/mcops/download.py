"""限速下载：备份文件 / restic 快照经面板下载。

面板经 SSH 隧道 → frps 云主机 → WireGuard 到浏览器，和玩家共用云主机的出口带宽
（10-09 一次不限速 scp 就让玩家掉线了），所以服务端按 rate_kib 匀速发送，并且同一时间只允许一个下载。

- 文件：支持单段 Range（浏览器断点续传），返回 206 + Content-Range。
- 流（restic dump）：大小未知，不支持续传；客户端断开时子进程被杀掉。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

log = logging.getLogger(__name__)

CHUNK = 64 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")

_slot: dict[str, float | None] = {"beat": None}
"""全局只允许一个下载在跑（多个并发下载会叠加带宽）。

用心跳而不是锁：客户端在响应开始前就断开时 Starlette 可能根本不启动 body 生成器，
它的 finally 不会执行，锁就永远不放了。心跳超过 STALE_S 没更新就视为空闲。
"""
STALE_S = 60.0


class DownloadBusy(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "已有一个备份正在下载，请等它结束后再试（同时下载会占满玩家带宽）")


def acquire_slot() -> None:
    beat = _slot["beat"]
    if beat is not None and time.monotonic() - beat < STALE_S:
        raise DownloadBusy()
    _slot["beat"] = time.monotonic()


def release_slot() -> None:
    _slot["beat"] = None


def _heartbeat() -> None:
    _slot["beat"] = time.monotonic()


class Throttle:
    """按平均速率限速：已发送字节数超过 rate × 已用时间时睡到对齐。rate_bps=0 不限速。"""

    def __init__(self, rate_bps: int) -> None:
        self.rate = rate_bps
        self.t0 = time.monotonic()
        self.sent = 0

    async def __call__(self, n: int) -> None:
        self.sent += n
        if self.rate <= 0:
            return
        ahead = self.sent / self.rate - (time.monotonic() - self.t0)
        if ahead > 0:
            await asyncio.sleep(ahead)


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """'bytes=a-b' → (start, end) 闭区间；无 Range 返回 None。不支持多段；越界抛 416。"""
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        raise HTTPException(416, "不支持的 Range", headers={"Content-Range": f"bytes */{size}"})
    a, b = m.groups()
    if a == "":  # bytes=-N：最后 N 字节
        start, end = max(size - int(b), 0), size - 1
    else:
        start, end = int(a), (int(b) if b else size - 1)
    end = min(end, size - 1)
    if start > end or start >= size:
        raise HTTPException(416, "Range 越界", headers={"Content-Range": f"bytes */{size}"})
    return start, end


def _disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"


def file_response(path: Path, rate_kib: int, range_header: str | None) -> StreamingResponse:
    """调用方需先 acquire_slot()；流结束（含客户端断开）时释放。"""
    size = path.stat().st_size
    try:
        rng = parse_range(range_header, size)
    except HTTPException:
        release_slot()
        raise
    start, end = rng if rng is not None else (0, size - 1)
    f = path.open("rb")  # 先打开：下载中途备份被轮换删除也不影响（Linux 上 fd 仍有效）

    async def body() -> AsyncIterator[bytes]:
        throttle = Throttle(rate_kib * 1024)
        left = end - start + 1
        try:
            await asyncio.to_thread(f.seek, start)
            while left > 0:
                chunk = await asyncio.to_thread(f.read, min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                _heartbeat()
                yield chunk
                await throttle(len(chunk))
        finally:
            f.close()
            release_slot()

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": _disposition(path.name),
        "Cache-Control": "no-store",
    }
    if rng is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        body(),
        status_code=206 if rng is not None else 200,
        media_type="application/gzip" if path.name.endswith(".gz") else "application/octet-stream",
        headers=headers,
    )


def process_response(
    proc: asyncio.subprocess.Process, filename: str, media_type: str, rate_kib: int
) -> StreamingResponse:
    """把子进程 stdout 限速转给浏览器。调用方已 acquire_slot()；结束 / 断开时杀进程并释放。"""
    assert proc.stdout is not None
    stdout = proc.stdout

    async def body() -> AsyncIterator[bytes]:
        throttle = Throttle(rate_kib * 1024)
        try:
            while chunk := await stdout.read(CHUNK):
                _heartbeat()
                yield chunk
                await throttle(len(chunk))
            rc = await proc.wait()
            if rc != 0:
                # 已经开始发送，没法再改状态码；截断的文件解压时会报错，日志里留原因
                err = b""
                if proc.stderr is not None:
                    err = await proc.stderr.read()
                log.error("导出中途失败（%s）：%s", rc, err.decode(errors="replace")[-500:])
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            release_slot()

    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={"Content-Disposition": _disposition(filename), "Cache-Control": "no-store"},
    )
