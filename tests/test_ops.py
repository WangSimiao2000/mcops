"""M3：冷备份 / 保留策略 / 定时重启流程 / 崩溃自愈。"""

from __future__ import annotations

import asyncio
import os
import tarfile
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from mcops.backup import (
    BackupConfig,
    BackupError,
    clean_backups,
    create_backup,
    ensure_space,
    list_backups,
)
from mcops.config import ServerConfig
from mcops.db import open_db
from mcops.events import Event, EventBus
from mcops.ops import Ops, OpsConfig
from mcops.rcon import RconClient
from mcops.server import ServerController, State
from tests.fakes import FakeBackend, FakeRconServer

CST = timezone(timedelta(hours=8))


def make_game(tmp: Path) -> Path:
    g = tmp / "GameFile"
    for d in (
        "world/region",
        "mods",
        "config",
        "EasyAuth",
        ".fabric/server",
        ".fabric/remappedJars",
        "logs",
    ):
        (g / d).mkdir(parents=True)
    (g / "world/region/r.0.0.mca").write_bytes(b"x" * 1000)
    (g / ".fabric/server/launch.jar").write_bytes(b"jar")
    (g / ".fabric/remappedJars/huge.jar").write_bytes(b"cache")
    (g / "logs/latest.log").write_text("log")
    (g / "whitelist.json").write_text("[]")
    (g / "server.properties").write_text("x=1")
    return g


async def test_backup_contents_and_save_cycle(tmp_path: Path) -> None:
    g = make_game(tmp_path)
    cfg = BackupConfig(g, tmp_path / "backups")
    cmds: list[str] = []

    async def cmd(c: str) -> str:
        cmds.append(c)
        return ""

    p = await create_backup(cfg, running=True, command=cmd)
    assert p.name.startswith("mc-backup-") and p.name.endswith(".tar.gz")
    with tarfile.open(p) as t:
        names = set(t.getnames())
    assert "world/region/r.0.0.mca" in names and "whitelist.json" in names
    assert ".fabric/server/launch.jar" in names  # .fabric 必须备份
    assert not any(n.startswith(".fabric/remappedJars") for n in names)
    assert not any(n.startswith("logs") for n in names)
    assert cmds == ["save-off", "save-all flush", "save-on"]


async def test_save_on_restored_even_if_tar_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    g = make_game(tmp_path)
    cmds: list[str] = []

    async def cmd(c: str) -> str:
        cmds.append(c)
        return ""

    import mcops.backup as b

    def boom(*a: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(b, "_make_tar", boom)
    with pytest.raises(OSError):
        await create_backup(BackupConfig(g, tmp_path / "bk"), running=True, command=cmd)
    assert cmds[-1] == "save-on"


def _fake_backups(d: Path, ages_days: list[float]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i, age in enumerate(ages_days):
        p = d / f"mc-backup-2026010{i}_050000.tar.gz"
        p.write_bytes(b"x")
        os.utime(p, (now - age * 86400, now - age * 86400))


def test_clean_respects_min_keep(tmp_path: Path) -> None:
    d = tmp_path / "bk"
    _fake_backups(d, [1, 2, 9, 10, 11])
    cfg = BackupConfig(tmp_path, d, keep_days=7, min_keep=3)
    deleted = clean_backups(cfg)
    assert len(deleted) == 2 and len(list_backups(d)) == 3  # 3 个过期，但只能删到剩 3 份
    _fake_backups(tmp_path / "bk2", [20, 30])
    assert clean_backups(BackupConfig(tmp_path, tmp_path / "bk2", min_keep=3)) == []


def test_ensure_space_refuses_below_min_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    d = tmp_path / "bk"
    _fake_backups(d, [1, 2, 3, 4])
    monkeypatch.setattr("mcops.backup.shutil.disk_usage", lambda p: type("U", (), {"free": 0})())
    with pytest.raises(BackupError, match="拒绝删除"):
        ensure_space(BackupConfig(make_game(tmp_path), d, min_keep=3))
    assert len(list_backups(d)) == 3  # 删到 min_keep 为止


class Env:
    def __init__(
        self,
        ops: Ops,
        srv: ServerController,
        backend: FakeBackend,
        rcon: FakeRconServer,
        bus: EventBus,
    ) -> None:
        self.ops, self.srv, self.backend, self.rcon, self.bus = ops, srv, backend, rcon, bus


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    rcon = FakeRconServer("pw", lambda c: "ok")
    async with rcon.running():
        engine = await open_db(tmp_path / "t.db")
        bus = EventBus(engine)
        g = make_game(tmp_path)
        cfg = ServerConfig(
            game_dir=g, jar="s.jar", rcon_port=rcon.port, start_ready_timeout_s=5, stop_timeout_s=2
        )
        backend = FakeBackend()
        srv = ServerController(cfg, backend, RconClient("127.0.0.1", rcon.port, "pw"), bus, CST)
        alerts: list[tuple[str, str]] = []

        async def raise_alert(rule: str, ts: datetime, title: str, *a: Any, **k: Any) -> None:
            alerts.append((rule, title))

        async def fast_sleep(s: float) -> None:
            await asyncio.sleep(0)

        announced: list[dict[str, Any]] = []

        async def announce(d: dict[str, Any]) -> None:
            announced.append(d)

        ops = Ops(
            engine,
            bus,
            srv,
            BackupConfig(g, tmp_path / "backups"),
            OpsConfig(warn_minutes=2, crash_threshold=3),
            CST,
            announce_maintenance=announce,
            raise_alert=raise_alert,
            sleep=fast_sleep,
        )
        ops.alerts, ops.announced = alerts, announced  # type: ignore[attr-defined]
        yield Env(ops, srv, backend, rcon, bus)
        await engine.dispose()


async def _running(env: Env) -> None:
    await env.srv.start()
    env.srv._ready.set()
    await env.srv.wait_state(State.RUNNING, timeout=5)


async def test_scheduled_restart_flow(env: Env) -> None:
    await _running(env)

    async def stopper() -> None:  # FakeBackend 收到 RCON stop 后"进程退出"
        while "stop" not in env.rcon.commands:
            await asyncio.sleep(0.02)
        env.backend.alive = False
        while env.srv.state is not State.STARTING:
            await asyncio.sleep(0.02)
        env.srv._ready.set()

    t = asyncio.create_task(stopper())
    run_id = await env.ops.jobs.run("scheduled_restart", "test", env.ops.scheduled_restart)
    await t
    (h,) = await env.ops.jobs.history()
    assert h["id"] == run_id and h["status"] == "ok", h["log"]
    assert h["result"]["backup"].startswith("mc-backup-")
    says = [c for c in env.rcon.commands if c.startswith("say ")]
    assert says == [
        "say §e[自动维护] 服务器将在2分钟后重启",
        "say §c[自动维护] 服务器将在1分钟后重启",
        "say §c[自动维护] 服务器将在10秒后重启！",
    ]
    assert env.ops.announced[0]["warn_minutes"] == 2  # type: ignore[attr-defined]
    assert env.srv.state is State.RUNNING and env.backend.started == 2
    assert env.srv.planned_by is None  # 开服后清除


async def test_restart_hooks_run_while_stopped_and_failures_dont_block(env: Env) -> None:
    await _running(env)
    seen: list[State] = []

    async def hook(note: Callable[[str], None]) -> None:
        seen.append(env.srv.state)
        note("hook ran")

    async def broken(_note: Callable[[str], None]) -> None:
        raise RuntimeError("boom")

    env.ops.restart_hooks += [broken, hook]

    async def stopper() -> None:
        while "stop" not in env.rcon.commands:
            await asyncio.sleep(0.02)
        env.backend.alive = False
        while env.srv.state is not State.STARTING:
            await asyncio.sleep(0.02)
        env.srv._ready.set()

    t = asyncio.create_task(stopper())
    await env.ops.jobs.run("scheduled_restart", "test", env.ops.scheduled_restart)
    await t
    (h,) = await env.ops.jobs.history()
    assert h["status"] == "ok"
    assert "停服钩子失败：RuntimeError: boom" in h["log"] and "hook ran" in h["log"]
    assert seen and seen[0] is not State.RUNNING  # 钩子在停服窗口里执行
    assert env.srv.state is State.RUNNING


async def test_planned_stop_is_not_a_crash(env: Env) -> None:
    await _running(env)
    await env.srv.stop(who="web")
    assert env.srv.planned_by == "web"


async def test_watchdog_autorestart_then_suspend(env: Env) -> None:
    for i in range(2):
        env.srv.state = State.CRASHED
        await env.ops._on_crash(Event("server_crashed", "server", ts=datetime.now(UTC)))
        assert env.backend.started == i + 1
        env.backend.alive = False
    env.srv.state = State.CRASHED
    await env.ops._on_crash(Event("server_crashed", "server", ts=datetime.now(UTC)))
    assert env.backend.started == 2  # 第 3 次：不再拉起
    assert env.ops.autorestart_suspended
    rules = [r for r, _ in env.ops.alerts]  # type: ignore[attr-defined]
    assert rules == ["server_crash", "server_crash", "server_crash_loop"]
    env.ops.reset_watchdog()
    assert not env.ops.autorestart_suspended


def test_next_restart_time(env: Env) -> None:
    now = datetime(2026, 10, 9, 14, 0, tzinfo=CST)
    assert env.ops.next_restart(now) == datetime(2026, 10, 10, 5, 0, tzinfo=CST)
    assert env.ops.next_restart(datetime(2026, 10, 9, 4, 59, tzinfo=CST)) == datetime(
        2026, 10, 9, 5, 0, tzinfo=CST
    )
    env.ops.cfg.restart_time = None
    assert env.ops.next_restart(now) is None


async def test_autostart_skips_when_not_installed(env: Env) -> None:
    await env.ops.autostart()
    assert env.backend.started == 0  # 还没装服务端（没有 jar）：不自启，等安装向导


async def test_autostart_respects_manual_stop(env: Env) -> None:
    (env.srv.cfg.game_dir / env.srv.cfg.jar).write_bytes(b"jar")
    await env.ops.autostart()
    assert env.backend.started == 1  # 默认期望运行：开机自启
    env.srv._ready.set()
    await env.srv.wait_state(State.RUNNING, timeout=5)
    env.backend.alive = False
    env.srv.state = State.STOPPED
    await env.ops.set_desired("stopped")
    await env.ops.autostart()
    assert env.backend.started == 1  # 手动关过服：不自启


def test_watchdog_alert_rules_registered() -> None:
    # 回归：生产切换时 watchdog 因 RULES 缺少这两条而 KeyError 崩溃
    from mcops.security import RULES

    assert RULES["server_crash"].severity == "warning"
    assert RULES["server_crash_loop"].severity == "critical"


async def test_post_ready_hooks_on_start_not_on_adopt(env: Env) -> None:
    fired: list[str] = []

    async def pre() -> None:
        fired.append("pre")

    async def post() -> None:
        await env.srv.rcon.command("setworldspawn 1 2 3")
        fired.append("post")

    env.srv.pre_start_hooks.append(pre)
    env.srv.post_ready_hooks.append(post)
    await _running(env)
    for _ in range(100):
        if "post" in fired:
            break
        await asyncio.sleep(0.02)
    assert fired == ["pre", "post"] and "setworldspawn 1 2 3" in env.rcon.commands
    fired.clear()
    env.srv.state = State.STOPPED
    await env.srv.adopt_existing()  # 进程仍在：接管，不触发钩子
    await asyncio.sleep(0.1)
    assert env.srv.state is State.RUNNING and fired == []


async def test_last_job_start(env: Env) -> None:
    assert await env.ops._last_job_start("restic_backup") is None

    async def ok(note: Any) -> dict[str, Any]:
        return {}

    await env.ops.jobs.run("restic_backup", "test", ok)
    last = await env.ops._last_job_start("restic_backup")
    assert last is not None and (datetime.now(UTC) - last).total_seconds() < 10


async def test_manual_backup_announces(env: Env) -> None:
    await _running(env)
    await env.ops.jobs.run("backup", "test", env.ops.manual_backup)
    tells = [c for c in env.rcon.commands if c.startswith("tellraw @a")]
    assert len(tells) == 2 and "完整备份" in tells[0] and '"color": "green"' in tells[1]


async def test_job_runner_publishes_ui_events(tmp_path: Path) -> None:
    """任务开始/结束推一条不落库的事件，在线面板据此刷新任务和备份列表。"""
    from mcops.ops import JobRunner

    engine = await open_db(tmp_path / "t.db")
    seen: list[Event] = []
    runner = JobRunner(engine, seen.append)

    async def ok(note: Any) -> dict[str, Any]:
        return {"x": 1}

    async def boom(note: Any) -> None:
        raise RuntimeError("x")

    await runner.run("manual_backup", "manual", ok)
    await runner.run("manual_backup", "manual", boom)
    assert [(e.type, e.data.get("status")) for e in seen] == [
        ("job_started", None),
        ("job_finished", "ok"),
        ("job_started", None),
        ("job_finished", "failed"),
    ]
    assert all(e.id is None for e in seen)  # 未落库
    await engine.dispose()


async def test_restart_recovers_server_even_if_backup_fails(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _running(env)
    import mcops.ops as ops_mod

    async def boom(*a: Any, **k: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(ops_mod, "create_backup", boom)

    async def stopper() -> None:
        while "stop" not in env.rcon.commands:
            await asyncio.sleep(0.02)
        env.backend.alive = False
        while env.srv.state is not State.STARTING:
            await asyncio.sleep(0.02)
        env.srv._ready.set()

    t = asyncio.create_task(stopper())
    run_id = await env.ops.jobs.run("scheduled_restart", "test", env.ops.scheduled_restart)
    await t
    (h,) = await env.ops.jobs.history()
    assert h["id"] == run_id and h["status"] == "failed"  # 任务记为失败
    assert env.srv.state is State.RUNNING  # 但服务器被开回来了
    assert env.srv.planned_by is None


async def test_mod_exclusive_blocks_concurrent_job(env: Env) -> None:
    from mcops.server import ConflictError

    async with env.ops.jobs.exclusive("mod_update"):
        assert env.ops.jobs.running == "mod_update"
        with pytest.raises(ConflictError):
            await env.ops.jobs.run("backup", "test", env.ops.manual_backup)
    assert env.ops.jobs.running is None  # 退出后锁释放


async def test_recover_after_restart_marks_interrupted_and_save_on(env: Env) -> None:
    from sqlalchemy import insert as _insert

    from mcops import db

    async with env.ops.engine.begin() as conn:
        await conn.execute(
            _insert(db.job_runs).values(
                job="scheduled_restart",
                trigger="schedule",
                started_at=datetime.now(UTC),
                status="running",
                log="",
                result={},
            )
        )
    await _running(env)
    env.rcon.commands.clear()
    await env.ops.recover_after_restart()
    rows = await env.ops.jobs.history()
    assert all(r["status"] != "running" for r in rows)
    assert any(r["status"] == "interrupted" for r in rows)
    assert "save-on" in env.rcon.commands  # 接管运行中的服务端会补发 save-on
