"""公开状态推送：定时把脱敏的服务器状态写成 status.json，推到一个开了 GitHub Pages 的仓库。

博客等公开页面（例如首页的「服务器」板块）读这份文件。服务器本身不开放任何入站端口，
博客也从不直连小主机，被刷爆的只会是 GitHub Pages。

- 只公开汇总信息：在线状态、在线人数（不含 carpet 假人）、上限、版本、运行时长、最近 24 小时的人数曲线。
  不含玩家名、IP、服务器地址。
- 仓库只保留最新一次提交（每次都是新的孤儿提交 + 强推），历史不会无限增长；曲线数据存在文件自己里。
- 用一把只对该仓库有写权限的部署密钥（deploy key）推送，不需要 GitHub 账号令牌。
- 每 interval_s（默认 10 分钟）检查一次，曲线点照常记在本地文件里；只有公开字段（在线、状态、人数、
  上限、版本、开服日期）变了才推送，没变化时每 heartbeat_s（默认 1 小时）保底推一次，让页面知道
  小主机还活着。每次推送都会触发一次 Pages 部署，Pages 部署偶尔会卡十来分钟，推得太密会一次接一次被顶掉。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HISTORY_HOURS = 24
_LOG_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-\d+\.log(?:\.gz)?$")


def detect_since(game_dir: Path) -> date | None:
    """开服日期：游戏目录 logs/ 里最早的归档日志（2026-04-18-1.log.gz 这种）。"""
    try:
        days = [m.group(1) for f in os.listdir(game_dir / "logs") if (m := _LOG_DATE.match(f))]
    except OSError:
        return None
    return date.fromisoformat(min(days)) if days else None


def build_payload(
    status: dict[str, Any],
    previous: dict[str, Any] | None,
    now: datetime | None = None,
    since: date | None = None,
    heartbeat_s: float | None = None,
) -> dict[str, Any]:
    """由 ServerController.status_snapshot() 生成公开数据，并把本次人数接到历史曲线后面。"""
    now = now or datetime.now(UTC)
    running = status.get("state") == "running"
    players = status.get("players") or {}
    online = players.get("real_count", players.get("online")) if running else None
    point = online if online is not None else 0
    history: list[list[Any]] = []
    cutoff = now - timedelta(hours=HISTORY_HOURS)
    for item in (previous or {}).get("history") or []:
        try:
            ts, n = item
            if datetime.fromisoformat(ts) >= cutoff and isinstance(n, int):
                history.append([ts, n])
        except (TypeError, ValueError):
            continue
    stamp = now.replace(microsecond=0).isoformat()
    history.append([stamp, point])
    return {
        "online": running,
        "state": status.get("state") or "unknown",
        "players_online": online,
        "players_max": players.get("max") if running else None,
        "version": status.get("mc_version"),
        "uptime_s": status.get("uptime_s") if running else None,  # 本次连续运行
        "since": since.isoformat() if since else None,  # 开服日期，重启不清零
        "updated_at": stamp,
        # 没变化时最久多少秒推一次：页面据此判断「失联」，不再假定 10 分钟一次
        "heartbeat_s": int(heartbeat_s) if heartbeat_s else None,
        "history": history,
    }


# 这些字段变了才值得推送；updated_at、uptime_s、曲线每次都在变，不算
_CHANGE_KEYS = ("online", "state", "players_online", "players_max", "version", "since")


def change_key(payload: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(payload.get(k) for k in _CHANGE_KEYS)


class StatusPublisher:
    def __init__(
        self,
        snapshot: Callable[[], dict[str, Any]],
        repo_dir: Path,
        remote: str,
        ssh_key: Path,
        known_hosts: Path,
        branch: str = "main",
        interval_s: float = 600.0,
        since: Callable[[], date | None] = lambda: None,
        heartbeat_s: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.snapshot = snapshot
        self.since = since
        self.repo_dir = repo_dir
        self.remote = remote
        self.ssh_key = ssh_key
        self.known_hosts = known_hosts
        self.branch = branch
        self.interval_s = interval_s
        self.heartbeat_s = max(heartbeat_s, interval_s)
        self.clock = clock
        self.last_ok: float | None = None  # 最近一次推送成功的时间
        self.last_error: str | None = None
        self._pushed: tuple[Any, ...] | None = (
            None  # 最近一次推送成功时的公开字段；进程重启后为空，先推一次
        )

    def _env(self) -> dict[str, str]:
        # 独立的 known_hosts：首次连接记下 github.com 的主机密钥，之后变了就拒绝（不碰用户自己的 ~/.ssh）
        ssh = [
            "ssh", "-i", str(self.ssh_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts}", "-o", "StrictHostKeyChecking=accept-new",
        ]  # fmt: skip
        return {
            **os.environ,
            "GIT_SSH_COMMAND": shlex.join(ssh),
            "GIT_AUTHOR_NAME": "mc-ops",
            "GIT_AUTHOR_EMAIL": "mc-ops@localhost",
            "GIT_COMMITTER_NAME": "mc-ops",
            "GIT_COMMITTER_EMAIL": "mc-ops@localhost",
        }

    async def _git(self, *args: str, check: bool = True) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=self.repo_dir,
            env=self._env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} 失败：{err.decode(errors='replace').strip()[-300:]}")
        return out.decode(errors="replace")

    async def _ensure_repo(self) -> None:
        if (self.repo_dir / ".git").is_dir():
            return
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        await self._git("init", "-q", "-b", self.branch)
        await self._git("remote", "add", "origin", self.remote)

    def _read_previous(self) -> dict[str, Any] | None:
        try:
            data = json.loads((self.repo_dir / "status.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _due(self, payload: dict[str, Any]) -> bool:
        if self._pushed is None or self.last_ok is None:
            return True
        if change_key(payload) != self._pushed:
            return True
        # 留一点余量：检查间隔有抖动，免得刚好差几秒拖到下一轮、变成 70 分钟一次
        return self.clock() - self.last_ok >= self.heartbeat_s - 60

    async def tick(self) -> tuple[dict[str, Any], bool]:
        """检查一次：曲线点总是写进本地文件；公开字段变了或到了保底时间才推送。返回 (数据, 是否推送)。"""
        await self._ensure_repo()
        payload = build_payload(
            self.snapshot(), self._read_previous(), since=self.since(), heartbeat_s=self.heartbeat_s
        )
        due = self._due(payload)
        self._write(payload)
        if due:
            await self._push(payload)
        return payload, due

    async def publish_once(self) -> dict[str, Any]:
        """不管有没有变化，立即推送一次。"""
        await self._ensure_repo()
        payload = build_payload(
            self.snapshot(), self._read_previous(), since=self.since(), heartbeat_s=self.heartbeat_s
        )
        self._write(payload)
        await self._push(payload)
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        (self.repo_dir / "status.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (self.repo_dir / ".nojekyll").touch()
        readme = self.repo_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                "# mc-status\n\n由 mc-ops 自动生成的公开服务器状态，只保留最新一次提交。\n",
                encoding="utf-8",
            )

    async def _push(self, payload: dict[str, Any]) -> None:
        # 孤儿提交 + 强推：远端始终只有一个提交
        await self._git("checkout", "-q", "--orphan", "publish")
        await self._git("add", "-A")
        await self._git("commit", "-q", "-m", f"status {payload['updated_at']}")
        await self._git("branch", "-q", "-D", self.branch, check=False)
        await self._git("branch", "-q", "-m", self.branch)
        await self._git("push", "-q", "--force", "origin", f"{self.branch}:{self.branch}")
        self.last_ok = self.clock()
        self.last_error = None
        self._pushed = change_key(payload)

    async def loop(self) -> None:
        await asyncio.sleep(30)  # 等接管 / 首次状态轮询完成
        while True:
            try:
                p, pushed = await self.tick()
                if pushed:
                    log.info(
                        "公开状态已推送：online=%s players=%s", p["online"], p["players_online"]
                    )
                else:
                    log.debug("公开状态无变化，跳过推送")
            except Exception as e:  # 推送失败不影响其他功能，下一轮再试
                self.last_error = str(e)
                log.warning("公开状态推送失败：%s", e)
            await asyncio.sleep(self.interval_s)
