"""受监督的后台任务：单个模块崩溃会被记录并按指数退避重启，不拖垮整个 agent。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_LOOSE: set[asyncio.Task[Any]] = set()


def spawn_logged(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
    """不归 Supervisor 管的短任务：保留强引用防被 GC，异常写日志而不是静默丢失。"""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _LOOSE.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _LOOSE.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("后台任务 %s 失败", name, exc_info=t.exception())

    task.add_done_callback(_done)
    return task


@dataclass(slots=True)
class ModuleState:
    state: str = "starting"  # starting / running / failed / stopped
    detail: str | None = None
    restarts: int = 0

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {"state": self.state, "restarts": self.restarts}
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class Supervisor:
    max_backoff_s: float = 60.0
    modules: dict[str, ModuleState] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _oneshots: set[asyncio.Task[Any]] = field(default_factory=set)

    def spawn(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        st = self.modules.setdefault(name, ModuleState())
        self._tasks[name] = asyncio.create_task(self._run(name, factory, st), name=name)

    async def _run(
        self, name: str, factory: Callable[[], Awaitable[None]], st: ModuleState
    ) -> None:
        backoff = 1.0
        while True:
            st.state, st.detail = "running", None
            try:
                await factory()
                st.state = "stopped"
                return
            except asyncio.CancelledError:
                st.state = "stopped"
                raise
            except Exception as e:
                log.exception("模块 %s 崩溃，%.0fs 后重启", name, backoff)
                st.state, st.detail = "failed", f"{type(e).__name__}: {e}"
                st.restarts += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff_s)

    def run_once(self, name: str, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """一次性后台任务（面板触发的运维任务、重启等）：保留引用、记录异常，关停时一并收尾。"""
        task = asyncio.create_task(coro, name=name)
        self._oneshots.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._oneshots.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("后台任务 %s 失败", name, exc_info=t.exception())

        task.add_done_callback(_done)
        return task

    async def stop_all(self, grace_s: float = 20.0) -> None:
        """关停：常驻循环立即取消；一次性任务先给 grace_s 秒自然结束（例如备份收尾），再取消。

        取消会让 paused_saves 等上下文的 finally 执行，服务端不会被留在 save-off 状态。
        """
        for t in self._tasks.values():
            t.cancel()
        if self._oneshots:
            _, pending = await asyncio.wait(set(self._oneshots), timeout=grace_s)
            for t in pending:
                log.warning("关停时取消未完成的任务 %s", t.get_name())
                t.cancel()
        for t in [*self._tasks.values(), *self._oneshots]:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
