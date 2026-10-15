"""服务器生命周期状态机 + 日志摄取。

就绪判定沿用旧系统经验："进程存活 ≠ 启动成功"，
必须看到本次启动之后的 `Done (` 才算 running；进程提前退出或超时都算失败。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, tzinfo
from enum import StrEnum
from pathlib import Path
from typing import Any

from mcops import logparse, slp
from mcops.backend import Backend
from mcops.config import ServerConfig
from mcops.events import Broadcaster, Event, EventBus
from mcops.logtail import follow
from mcops.rcon import RconClient
from mcops.slp import ANONYMOUS
from mcops.supervisor import spawn_logged

log = logging.getLogger(__name__)

# watch_process 连续多少次（每次间隔 1 秒）查不到进程才判定崩溃
CRASH_CONFIRM = 5


class State(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


class ConflictError(RuntimeError):
    """当前状态不允许该操作（API 映射为 409）。"""


class ServerController:
    def __init__(
        self,
        cfg: ServerConfig,
        backend: Backend,
        rcon: RconClient,
        bus: EventBus,
        local_tz: tzinfo,
        observe: bool = False,
    ) -> None:
        self.observe = observe
        self.cfg = cfg
        self.backend = backend
        self.rcon = rcon
        self.bus = bus
        self.tz = local_tz
        self.state = State.STOPPED
        self.started_at: float | None = None
        self.mc_version: str | None = None
        self.log_lines: Broadcaster[dict[str, Any]] = Broadcaster(maxsize=5000)
        self.status_updates: Broadcaster[dict[str, Any]] = Broadcaster()
        self._ready = asyncio.Event()
        self.pre_start_hooks: list[Callable[[], Awaitable[None]]] = []
        """启动进程前执行（如同步 MOTD 到配置文件）。失败只记日志，不阻止启动。"""
        self.post_ready_hooks: list[Callable[[], Awaitable[None]]] = []
        """本次启动就绪后执行（如 setworldspawn）。接管已运行的进程时不执行。"""
        self._op_lock = asyncio.Lock()
        self._last_players: slp.PingResult | None = None
        self.roster: Callable[[], Awaitable[tuple[list[str], list[str]]]] | None = None
        """在线名单来源（会话追踪）→ (真人, 假人)。服务器列表样本里假人只显示为 Anonymous Player。"""
        self._roster: tuple[list[str], list[str]] | None = None
        self._pid: int | None = None
        self.planned_by: str | None = None
        """当前这次停机的发起者（scheduled-restart / web / watchdog…）；None 表示非计划停机。"""

    # ---- 状态 -------------------------------------------------------------

    def _set_state(self, st: State) -> None:
        if st is self.state:
            return
        log.info("server state %s -> %s", self.state, st)
        self.state = st
        self.status_updates.publish({"kind": "status", "status": self.status_snapshot()})

    def status_snapshot(self) -> dict[str, Any]:
        players = None
        if self.state is State.RUNNING and self._last_players is not None:
            p = self._last_players
            players = {"online": p.online, "max": p.max, "names": p.names}
            if self._roster is not None:
                real, fake = self._roster
                sample = [n for n in p.names if n and n != ANONYMOUS and n not in fake]
                real = sample + [n for n in real if n not in sample]
                players.update(
                    names=real + fake,
                    real=real,
                    fake=fake,
                    real_count=max(p.online - len(fake), 0),
                    fake_count=len(fake),
                )
        uptime = (
            round(time.monotonic() - self.started_at)
            if self.started_at is not None and self.state in (State.RUNNING, State.STOPPING)
            else None
        )
        return {
            "state": self.state.value,
            "pid": self._pid,
            "uptime_s": uptime,
            "mc_version": self.mc_version,
            "players": players,
        }

    async def refresh(self) -> dict[str, Any]:
        self._pid = await self.backend.pid()
        if self.state is State.RUNNING:
            try:
                self._last_players = await slp.ping("127.0.0.1", self.cfg.port)
                self.mc_version = self._last_players.version or self.mc_version
            except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError):
                pass
            if self.roster is not None:
                try:
                    self._roster = await self.roster()
                except Exception:  # 数据库暂时不可用：退回只用服务器列表样本
                    log.exception("读取在线名单失败")
                    self._roster = None
        return self.status_snapshot()

    async def adopt_existing(self) -> None:
        """agent 启动时：如果服务端已在运行（agent 重启/升级），直接接管而不重启游戏。"""
        pid = await self.backend.pid()
        if pid is not None:
            self._pid = pid
            self.started_at = _proc_started_monotonic(pid)
            self._ready.set()
            self._set_state(State.RUNNING)
            await self.bus.emit(Event("server_adopted", "server", {"pid": pid}))

    # ---- 操作 -------------------------------------------------------------

    def deny_if_observe(self) -> None:
        if self.observe:
            raise ConflictError("观测模式：服务器仍由旧脚本管理，面板不执行控制操作")

    async def start(self) -> None:
        self.deny_if_observe()
        async with self._op_lock:
            if self.state not in (State.STOPPED, State.CRASHED):
                raise ConflictError(f"当前状态为 {self.state}，不能启动")
            if await self.backend.pid() is not None:
                raise ConflictError("检测到服务端进程已在运行")
            self._ready.clear()
            self.planned_by = None
            self._set_state(State.STARTING)
            await self.bus.emit(Event("server_starting", "server"))
            await _run_hooks("pre_start", self.pre_start_hooks)
            try:
                await self.backend.start()
            except Exception as e:
                self._set_state(State.CRASHED)
                await self.bus.emit(Event("server_start_failed", "server", {"error": str(e)}))
                raise
            self.started_at = time.monotonic()
        spawn_logged(self._await_ready(), "await_ready")

    async def _await_ready(self) -> None:
        deadline = time.monotonic() + self.cfg.start_ready_timeout_s
        while time.monotonic() < deadline:
            if self._ready.is_set():
                self._set_state(State.RUNNING)
                await self.refresh()
                self.status_updates.publish({"kind": "status", "status": self.status_snapshot()})
                await _run_hooks("post_ready", self.post_ready_hooks)
                return
            if await self.backend.pid() is None:
                # 进程提前退出（例如 .fabric 缺失导致安装器退化后退出）
                self._set_state(State.CRASHED)
                await self.bus.emit(
                    Event("server_start_failed", "server", {"error": "进程在就绪前退出"})
                )
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._ready.wait(), 1.0)
        await self.bus.emit(
            Event(
                "server_start_failed",
                "server",
                {"error": "就绪超时", "timeout_s": self.cfg.start_ready_timeout_s},
            )
        )
        self._set_state(State.CRASHED)

    async def stop(self, who: str = "web") -> None:
        self.deny_if_observe()
        async with self._op_lock:
            if self.state not in (State.RUNNING, State.STARTING):
                raise ConflictError(f"当前状态为 {self.state}，不能停止")
            self.planned_by = who
            self._set_state(State.STOPPING)
            await self.bus.emit(Event("server_stop_requested", "server", {"who": who}))
        spawn_logged(self._do_stop(), "do_stop")

    async def _do_stop(self) -> None:
        try:
            await self.rcon.command("stop")
        except Exception as e:  # RCON 不可达也要能关：退回 SIGTERM（MC 会保存后退出）
            log.warning("RCON stop 失败，改用 SIGTERM：%s", e)
            with contextlib.suppress(Exception):
                await self.backend.terminate()
        deadline = time.monotonic() + self.cfg.stop_timeout_s
        while time.monotonic() < deadline:
            if await self.backend.pid() is None:
                break
            await asyncio.sleep(0.5)
        else:
            await self.backend.kill()
            await self.bus.emit(Event("server_killed", "server", {"reason": "stop 超时"}))
        await self.rcon.close()
        self.started_at = None
        self._pid = None
        self._set_state(State.STOPPED)
        await self.bus.emit(Event("server_stopped", "server"))

    async def wait_state(self, *states: State, timeout: float = 300) -> State:
        deadline = time.monotonic() + timeout
        while self.state not in states:
            if time.monotonic() > deadline:
                raise TimeoutError(f"等待状态 {states} 超时，当前 {self.state}")
            await asyncio.sleep(0.5)
        return self.state

    async def say(self, text: str) -> None:
        if self.state is State.RUNNING and not self.observe:
            with contextlib.suppress(Exception):
                await self.rcon.command(f"say {text}")

    async def broadcast(self, text: str, color: str = "gray") -> None:
        """tellraw @a：玩家看到的是干净的一行（say 会带 [Rcon] 前缀）。失败不影响调用方。"""
        if self.state is State.RUNNING and not self.observe:
            payload = json.dumps({"text": text, "color": color}, ensure_ascii=False)
            with contextlib.suppress(Exception):
                await self.rcon.command(f"tellraw @a {payload}")

    async def restart(self, who: str = "web") -> None:
        self.deny_if_observe()
        await self.stop(who)
        await self.wait_state(State.STOPPED, timeout=self.cfg.stop_timeout_s + 30)
        await asyncio.sleep(2)  # 等端口释放（沿用旧 cmd_restart）
        await self.start()

    async def command(self, cmd: str) -> str:
        self.deny_if_observe()
        if self.state is not State.RUNNING:
            raise ConflictError("服务器未运行")
        return await self.rcon.command(cmd)

    # ---- 后台任务 ---------------------------------------------------------

    async def watch_process(self, interval_s: float = 1.0, confirm: int = CRASH_CONFIRM) -> None:
        """进程意外消失 → crashed（M1 的 watchdog 在此基础上做自愈与告警）。

        - 连续 ``confirm`` 次查不到进程才判崩溃：systemd 后端靠 ``systemctl --user show``
          取 PID，系统升级时 user manager 会 daemon-reexec，几秒内查询失败，不能当成崩溃
          （2026-10-15 06:01 就这样把正在运行的服务器误判为崩溃）。
        - 处于 crashed 却又查到了进程（误判、或自动重启撞上"进程已在运行"）→ 重新接管为
          running，面板不会一直卡在"已崩溃"。
        """
        misses = 0
        while True:
            await asyncio.sleep(interval_s)
            if self.observe:
                await self._observe_tick()
                continue
            if self.state is State.CRASHED:
                misses = 0
                await self._readopt_if_alive()
                continue
            if self.state is not State.RUNNING:
                misses = 0
                continue
            if await self.backend.pid() is not None:
                misses = 0
                continue
            misses += 1
            if misses < confirm:
                continue
            misses = 0
            self.started_at = None
            self._pid = None
            self._set_state(State.CRASHED)
            await self.bus.emit(Event("server_crashed", "server"))

    async def _readopt_if_alive(self) -> None:
        async with self._op_lock:
            if self.state is not State.CRASHED:
                return
            pid = await self.backend.pid()
            if pid is None:
                return
            self._pid = pid
            self.started_at = _proc_started_monotonic(pid)
            self._ready.set()
            self._set_state(State.RUNNING)
        log.warning("crashed 状态下发现服务端进程 %s 仍在运行，已重新接管", pid)
        await self.bus.emit(Event("server_adopted", "server", {"pid": pid, "reason": "recovered"}))

    async def _observe_tick(self) -> None:
        """观测模式：只跟随进程是否存在（旧脚本每天 05:00 计划重启，不能算崩溃）。"""
        pid = await self.backend.pid()
        if pid is not None and self.state is not State.RUNNING:
            self._pid, self.started_at = pid, _proc_started_monotonic(pid)
            self._set_state(State.RUNNING)
            await self.bus.emit(Event("server_observed_up", "server", {"pid": pid}))
        elif pid is None and self.state is State.RUNNING:
            self._pid = self.started_at = None
            self._set_state(State.STOPPED)
            await self.bus.emit(Event("server_observed_down", "server"))

    async def poll_status(self, interval_s: float = 5.0) -> None:
        while True:
            await asyncio.sleep(interval_s)
            before = self.status_snapshot()
            after = await self.refresh()
            if after != before:
                self.status_updates.publish({"kind": "status", "status": after})

    async def ingest_logs(self) -> None:
        tracker = logparse.DayTracker(datetime.now(self.tz).date())
        async for raw in follow(self.cfg.latest_log):
            day: date = tracker.feed(raw)
            if day < datetime.now(self.tz).date():  # 实时 tail 时始终用今天
                tracker.day = day = datetime.now(self.tz).date()
            self.log_lines.publish(
                {"kind": "log", "ts": datetime.now(UTC).isoformat(), "line": raw}
            )
            line = logparse.parse_line(raw, day, self.tz)
            if line is None:
                continue
            for pe in logparse.classify(line):
                if pe.type == "server_ready":
                    self._ready.set()
                elif pe.type == "server_stopping":
                    self._ready.clear()
                await self.bus.emit(Event(pe.type, "mc_log", pe.data, ts=pe.ts.astimezone(UTC)))


async def _run_hooks(stage: str, hooks: list[Callable[[], Awaitable[None]]]) -> None:
    for h in hooks:
        try:
            await h()
        except Exception:
            log.exception("%s 钩子 %s 失败", stage, getattr(h, "__name__", h))


def _proc_started_monotonic(pid: int) -> float:
    """进程真实启动时刻（换算到 time.monotonic 时间轴），让接管后的 uptime 不从 agent 重启算起。"""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        start_ticks = int(fields[19])  # 第 22 个字段 starttime（去掉 pid、comm 后下标 19）
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        age = uptime - start_ticks / os.sysconf("SC_CLK_TCK")
        return time.monotonic() - max(age, 0.0)
    except (OSError, ValueError, IndexError):
        return time.monotonic()
