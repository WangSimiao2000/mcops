"""M4：restic 增量备份（需要本机有 restic；没有则跳过）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mcops.restic import Restic, RetentionPolicy
from tests.test_ops import Env, env, make_game  # noqa: F401  (fixture 复用)

RESTIC = shutil.which("restic") or str(Path.home() / ".local/bin/restic")
pytestmark = pytest.mark.skipif(not Path(RESTIC).exists(), reason="restic 未安装")


def _restic(tmp: Path, game: Path) -> Restic:
    return Restic(
        RESTIC,
        str(tmp / "repo"),
        "test-pw",
        game,
        policy=RetentionPolicy(1, 1, 0, 0),
        timeout_s=120,
    )


async def test_restic_backup_incremental_and_excludes(tmp_path: Path) -> None:
    g = make_game(tmp_path)
    r = _restic(tmp_path, g)
    assert await r.ensure_repo() is True
    assert await r.ensure_repo() is False
    first = await r.backup(tags=["t"])
    assert first["files_new"] >= 4 and first["snapshot"]
    second = await r.backup(tags=["t"])
    assert (
        second["files_new"] == 0 and second["added_bytes"] < first["added_bytes"]
    )  # 增量：没变化几乎不新增
    snaps = await r.snapshots()
    assert len(snaps) == 2 and snaps[0]["tags"] == ["t"]
    # 排除项不在快照里，.fabric/server 在
    import asyncio
    import json

    proc = await asyncio.create_subprocess_exec(
        RESTIC,
        "ls",
        "--json",
        "latest",
        env=r._env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    paths = [json.loads(ln).get("path", "") for ln in out.decode().splitlines() if '"path"' in ln]
    assert any(p.endswith(".fabric/server/launch.jar") for p in paths)
    assert not any("remappedJars" in p or "/logs/" in p for p in paths)
    removed = await r.forget(prune=True)  # keep_hourly=1, keep_daily=1：同一小时的两个快照留 1 个
    assert removed == 1 and len(await r.snapshots()) == 1
    assert (await r.stats())["repo_bytes"] > 0


async def test_ops_restic_job_pauses_saves(env: Env, tmp_path: Path) -> None:  # noqa: F811
    from tests.test_ops import _running

    env.ops.restic = _restic(tmp_path, env.ops.backup_cfg.game_dir)
    await _running(env)
    run_id = await env.ops.jobs.run("restic_backup", "test", env.ops.restic_backup)
    (h,) = [x for x in await env.ops.jobs.history() if x["id"] == run_id]
    assert h["status"] == "ok", h["log"]
    assert h["result"]["snapshot"] and h["result"]["pruned"] is True
    assert [c for c in env.rcon.commands if c.startswith("save")] == [
        "save-off",
        "save-all flush",
        "save-on",
    ]
    tells = [c for c in env.rcon.commands if c.startswith("tellraw @a")]
    assert len(tells) == 2 and "正在进行自动备份" in tells[0] and "备份完成" in tells[1]
    assert env.rcon.commands.index(tells[0]) < env.rcon.commands.index(
        "save-off"
    )  # 先提醒再暂停保存
