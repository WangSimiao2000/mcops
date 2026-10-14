"""从快照回退：成功替换并开服、开服失败换回原文件、范围与仓库选择。需要本机有 restic。"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from mcops.ops import RESTORE_SCOPES
from mcops.restic import OffsiteTarget, Restic, ResticError
from tests.test_ops import Env, _running

RESTIC = shutil.which("restic") or str(Path.home() / ".local/bin/restic")
pytestmark = pytest.mark.skipif(not Path(RESTIC).exists(), reason="restic 未安装")


async def _cycle(env: Env, ready: bool) -> None:
    """模拟进程：收到 stop 就退出；回退后开服 ready=True 则就绪，否则一启动就崩、等换回后再就绪。"""
    while "stop" not in env.rcon.commands:
        await asyncio.sleep(0.02)
    env.backend.alive = False
    while env.backend.started < 2:
        await asyncio.sleep(0.02)
    if ready:
        env.srv._ready.set()
    else:
        env.backend.alive = False
        while env.backend.started < 3:
            await asyncio.sleep(0.02)
        env.srv._ready.set()


async def _setup(env: Env, tmp_path: Path) -> tuple[Path, str]:
    g = env.ops.backup_cfg.game_dir
    (g / "world" / "level.dat").write_bytes(b"monday")
    (g / "mods" / "a.jar").write_bytes(b"mod-v1")
    env.ops.restic = Restic(RESTIC, str(tmp_path / "repo"), "pw", g, timeout_s=120)
    await env.ops.restic.ensure_repo()
    snap = (await env.ops.restic.backup(tags=["auto"]))["snapshot"]
    # 之后世界被炸了、mod 也升级了
    (g / "world" / "level.dat").write_bytes(b"griefed")
    (g / "mods" / "a.jar").write_bytes(b"mod-v2")
    return g, snap


async def test_restore_world_only(ops_env: Env, tmp_path: Path) -> None:
    env = ops_env
    g, snap = await _setup(env, tmp_path)
    await _running(env)
    t = asyncio.create_task(_cycle(env, ready=True))
    run_id = await env.ops.jobs.run(
        "snapshot_restore", "test", env.ops.snapshot_restore(snap, "world", "local")
    )
    await t
    (h,) = [x for x in await env.ops.jobs.history() if x["id"] == run_id]
    assert h["status"] == "ok", h["log"]
    assert (g / "world" / "level.dat").read_bytes() == b"monday"  # 世界回退了
    assert (g / "mods" / "a.jar").read_bytes() == b"mod-v2"  # 只回退世界：mod 不动
    old = g / h["result"]["old_dir"]
    assert (old / "world" / "level.dat").read_bytes() == b"griefed"  # 被替换的现场保留
    assert any("回退世界到" in c for c in env.rcon.commands)  # 游戏内倒计时
    # 回退前给现状拍了快照，可以反悔
    assert env.ops.restic is not None
    tags = [s["tags"] for s in await env.ops.restic.snapshots()]
    assert ["pre-restore"] in tags
    assert not (old.parent / "new").exists()


async def test_restore_game_scope_and_rollback_on_failed_start(
    ops_env: Env, tmp_path: Path
) -> None:
    env = ops_env
    g, snap = await _setup(env, tmp_path)
    await _running(env)
    t = asyncio.create_task(_cycle(env, ready=False))
    with pytest.raises(RuntimeError, match="已换回原来的文件"):
        await env.ops.snapshot_restore(snap, "game", "local")(lambda _m: None)
    await t
    # 开服失败：世界和 mod 都换回回退前的样子，失败现场单独留存
    assert (g / "world" / "level.dat").read_bytes() == b"griefed"
    assert (g / "mods" / "a.jar").read_bytes() == b"mod-v2"
    (work,) = (g / ".mcops-backup").glob("restore-*")
    assert (work / "failed" / "world" / "level.dat").read_bytes() == b"monday"
    assert env.backend.started == 3
    assert ".fabric" not in RESTORE_SCOPES["game"]


async def test_restore_unknown_snapshot_or_repo(ops_env: Env, tmp_path: Path) -> None:
    env = ops_env
    g, _ = await _setup(env, tmp_path)
    with pytest.raises(ResticError, match="没有快照"):
        await env.ops.snapshot_restore("deadbeef", "world", "local")(lambda _m: None)
    assert (g / "world" / "level.dat").read_bytes() == b"griefed"  # 什么都没动
    with pytest.raises(KeyError):
        await env.ops.find_repo("nas")  # 没配置异地仓库


async def test_restore_from_offsite_repo(ops_env: Env, tmp_path: Path) -> None:
    """异地仓库（这里用本地目录充当）里的快照也能列出来并回退。"""
    env = ops_env
    g, _ = await _setup(env, tmp_path)
    nas = OffsiteTarget("NAS", str(tmp_path / "nas"), id="nas")
    assert env.ops.restic is not None
    await env.ops.restic.copy_to(nas, prune=False)

    async def targets() -> list[OffsiteTarget]:
        return [nas]

    env.ops.offsite_targets = targets
    (remote,) = await env.ops.restic.snapshots(await env.ops.find_repo("nas"))
    await _running(env)
    t = asyncio.create_task(_cycle(env, ready=True))
    await env.ops.snapshot_restore(remote["id"], "world", "nas")(lambda _m: None)
    await t
    assert (g / "world" / "level.dat").read_bytes() == b"monday"


async def test_restore_reports_progress(ops_env: Env, tmp_path: Path) -> None:
    """真实 restic --json：回调拿到字节进度，结束时 已恢复 == 总量。"""
    env = ops_env
    _g, snap = await _setup(env, tmp_path)
    assert env.ops.restic is not None
    seen: list[tuple[int, int]] = []
    dest = tmp_path / "out"
    await env.ops.restic.restore(
        snap, dest, ["world"], on_progress=lambda d, t: seen.append((d, t))
    )
    assert (dest / "world" / "level.dat").read_bytes() == b"monday"
    assert seen and seen[-1][0] == seen[-1][1] > 0
