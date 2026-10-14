"""向服务端发命令的通道。

- RconChannel：control 模式（M3 起），命令直接拿到返回值。
- LegacyMcShChannel：observe 模式（M2 过渡期，生产尚未开 RCON），沿用旧机器人的做法：
  调 `mc.sh player cmd ...`（tmux send-keys），再从 latest.log 新增部分回读结果。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Protocol

from mcops.rcon import RconClient

log = logging.getLogger(__name__)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class ServerChannel(Protocol):
    async def run(self, command: str) -> str | None:
        """执行服务端命令，返回服务端输出；拿不到输出时返回 None。"""
        ...


class RconChannel:
    def __init__(self, rcon: RconClient) -> None:
        self._rcon = rcon

    async def run(self, command: str) -> str | None:
        try:
            return await self._rcon.command(command)
        except Exception as e:
            log.warning("RCON 命令失败 %r：%s", command, e)
            return None


class LegacyMcShChannel:
    def __init__(self, mc_sh: Path, latest_log: Path, settle_s: float = 2.0) -> None:
        self.mc_sh, self.latest_log, self.settle_s = mc_sh, latest_log, settle_s
        self._lock = asyncio.Lock()

    async def _mc_sh(self, *args: str, timeout: float = 20.0) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.mc_sh),
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except (TimeoutError, OSError) as e:
            log.warning("mc.sh 调用失败：%s", e)
            return False
        if proc.returncode != 0:
            log.warning(
                "mc.sh 返回 %s：%s",
                proc.returncode,
                _ANSI_RE.sub("", out.decode(errors="replace"))[-300:],
            )
            return False
        return True

    async def run(self, command: str) -> str | None:
        if "\n" in command or "\r" in command:
            return None
        async with self._lock:
            try:
                mark = self.latest_log.stat().st_size
            except OSError:
                mark = 0
            if not await self._mc_sh("player", "cmd", *command.split()):
                return None
            await asyncio.sleep(self.settle_s)
            try:
                with open(self.latest_log, "rb") as f:
                    f.seek(mark)
                    chunk = f.read().decode("utf-8", "replace")
            except OSError as e:
                log.warning("读取服务端日志失败：%s", e)
                return None
        # 去掉日志行前缀，只留消息正文
        lines = [re.sub(r"^\[[^\]]+\] \[[^\]]+\]: ", "", ln) for ln in chunk.splitlines()]
        return "\n".join(lines)
