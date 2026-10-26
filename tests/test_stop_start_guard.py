"""关服不再卡在 stopping；启动前发现 mc-ops 之外的服务端就拒绝（2026-10-26 停服 5 小时）。"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
from datetime import UTC
from pathlib import Path

import pytest

from mcops.backend import SystemdBackend
from mcops.config import ServerConfig
from mcops.db import open_db
from mcops.events import EventBus
from mcops.rcon import RconClient
from mcops.server import ConflictError, ServerController, State
from tests.fakes import FakeBackend


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _hold_lock(path: Path) -> subprocess.Popen[str]:
    """另起一个进程用 fcntl 锁住文件（同一进程内的 POSIX 锁互不冲突，测不出来）。"""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl,os,time\n"
            f"fd=os.open({str(path)!r},os.O_RDWR);fcntl.lockf(fd,fcntl.LOCK_EX)\n"
            "print('locked',flush=True);time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )


class StubbornBackend(FakeBackend):
    """terminate 不起作用；kill 按设定报错，并可选择是否真的杀掉。"""

    def __init__(self, kill_works: bool) -> None:
        super().__init__()
        self.kill_works = kill_works

    async def terminate(self) -> None:
        pass

    async def kill(self) -> None:
        self.killed += 1
        if self.kill_works:
            self.alive = False
        raise RuntimeError("Failed to send signal SIGKILL to auxiliary processes: Invalid argument")


async def _controller(tmp_path: Path, backend: FakeBackend, port: int) -> ServerController:
    engine = await open_db(tmp_path / "mcops.db")
    return ServerController(
        ServerConfig(game_dir=tmp_path, jar="server.jar", port=port, stop_timeout_s=0.2),
        backend,
        RconClient("127.0.0.1", 1, ""),
        EventBus(engine),
        UTC,
    )


@pytest.fixture
def port() -> int:
    return _free_port()


async def _stop(s: ServerController) -> None:
    await s.stop("test")
    await s.wait_state(State.STOPPED, State.RUNNING, timeout=15)
    for _ in range(100):  # _do_stop 是后台任务：等它跑完再断言
        if s.state is not State.STOPPING:
            break
        await asyncio.sleep(0.05)


async def test_kill_error_but_process_gone_ends_stopped(tmp_path: Path, port: int) -> None:
    backend = StubbornBackend(kill_works=True)
    backend.alive = True
    s = await _controller(tmp_path, backend, port)
    await s.adopt_existing()
    await _stop(s)
    assert s.state is State.STOPPED
    assert backend.killed == 1
    assert await s.bus.recent(5, ["server_stopped"])


async def test_kill_fails_and_process_alive_returns_to_running(tmp_path: Path, port: int) -> None:
    backend = StubbornBackend(kill_works=False)
    backend.alive = True
    s = await _controller(tmp_path, backend, port)
    await s.adopt_existing()
    await s.stop("test")
    for _ in range(300):
        if s.state is not State.STOPPING:
            break
        await asyncio.sleep(0.05)
    assert s.state is State.RUNNING
    evs = await s.bus.recent(5, ["server_stop_failed"])
    assert evs and "强制结束失败" in evs[0].data["error"]


async def test_start_refused_when_port_already_listening(tmp_path: Path, port: int) -> None:
    srv = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", port)
    try:
        backend = FakeBackend()
        s = await _controller(tmp_path, backend, port)
        with pytest.raises(ConflictError, match="之外已有服务端"):
            await s.start()
        assert s.state is State.STOPPED
        assert backend.started == 0
        evs = await s.bus.recent(5, ["server_foreign_instance"])
        assert evs and str(port) in evs[0].data["reason"]
    finally:
        srv.close()
        await srv.wait_closed()


async def test_start_refused_when_session_lock_held_elsewhere(tmp_path: Path, port: int) -> None:
    (tmp_path / "server.properties").write_text("level-name=myworld\n", encoding="utf-8")
    lock = tmp_path / "myworld" / "session.lock"
    lock.parent.mkdir()
    lock.write_bytes(b"\xe2\x98\x83")
    holder = _hold_lock(lock)
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
        backend = FakeBackend()
        s = await _controller(tmp_path, backend, port)
        with pytest.raises(ConflictError, match=r"session\.lock"):
            await s.start()
        assert backend.started == 0
    finally:
        holder.kill()
        holder.wait()
    # 锁放开后照常启动
    assert await s.foreign_instance() is None
    await s.start()
    assert backend.started == 1


async def test_systemd_kill_error_tolerated_when_main_pid_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = SystemdBackend(ServerConfig(game_dir=tmp_path, jar="server.jar"))

    async def fake_systemctl(*args: str) -> str:
        if args[0] == "kill":
            raise RuntimeError("Failed to send signal SIGKILL to auxiliary processes")
        return "0"  # show MainPID → 0：主进程已不在

    monkeypatch.setattr(b, "_systemctl", fake_systemctl)
    await b.kill()  # 不抛


async def test_systemd_kill_error_raised_when_main_pid_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    b = SystemdBackend(ServerConfig(game_dir=tmp_path, jar="server.jar"))

    async def fake_systemctl(*args: str) -> str:
        if args[0] == "kill":
            raise RuntimeError("Access denied")
        return str(os.getpid())  # 主进程还活着

    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr(b, "_systemctl", fake_systemctl)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    with pytest.raises(RuntimeError, match="Access denied"):
        await b.kill()
