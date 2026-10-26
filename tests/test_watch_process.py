"""watch_process：短暂查不到进程不算崩溃；误判为 crashed 后能重新接管。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC
from pathlib import Path

import pytest

from mcops.config import ServerConfig
from mcops.db import open_db
from mcops.events import EventBus
from mcops.rcon import RconClient
from mcops.server import ServerController, State
from tests.fakes import FakeBackend


class FlakyBackend(FakeBackend):
    """模拟 systemctl --user show 在 user manager daemon-reexec 时短暂失败。"""

    def __init__(self) -> None:
        super().__init__()
        self.alive = True
        self.blackout = 0

    async def pid(self) -> int | None:
        if self.blackout > 0:
            self.blackout -= 1
            return None
        return await super().pid()


@pytest.fixture
async def srv(tmp_path: Path) -> AsyncIterator[tuple[ServerController, FlakyBackend]]:
    engine = await open_db(tmp_path / "mcops.db")
    backend = FlakyBackend()
    s = ServerController(
        ServerConfig(game_dir=tmp_path, jar="server.jar"),
        backend,
        RconClient("127.0.0.1", 1, ""),
        EventBus(engine),
        UTC,
    )
    await s.adopt_existing()
    assert s.state is State.RUNNING
    yield s, backend
    await engine.dispose()


async def _run(s: ServerController, ticks: int, confirm: int = 3) -> None:
    task = asyncio.create_task(s.watch_process(interval_s=0.001, confirm=confirm))
    await asyncio.sleep(0.001 * ticks + 0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_brief_pid_lookup_failure_is_not_a_crash(
    srv: tuple[ServerController, FlakyBackend],
) -> None:
    s, backend = srv
    backend.blackout = 2  # 少于 confirm=3 次
    await _run(s, 20)
    assert s.state is State.RUNNING


async def test_process_really_gone_is_crash(srv: tuple[ServerController, FlakyBackend]) -> None:
    s, backend = srv
    backend.alive = False
    await _run(s, 20)
    assert s.state is State.CRASHED


async def test_crashed_but_process_alive_is_readopted(
    srv: tuple[ServerController, FlakyBackend],
) -> None:
    s, backend = srv
    backend.blackout = 3  # 足以判崩溃，之后进程又能查到
    await _run(s, 40)
    assert s.state is State.RUNNING
    assert s.started_at is not None
