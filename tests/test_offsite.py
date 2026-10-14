"""异地副本：目标组装（不需要 restic）+ 真实 restic copy（本机有 restic 才跑）+ 任务失败告警。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from mcops.restic import (
    OffsiteTarget,
    Restic,
    ResticError,
    RetentionPolicy,
    s3_target,
    smb_target,
)
from tests.test_ops import Env, make_game

RESTIC = shutil.which("restic") or str(Path.home() / ".local/bin/restic")
needs_restic = pytest.mark.skipif(not Path(RESTIC).exists(), reason="restic 未安装")


def test_s3_target_cos_uses_virtual_host_and_region() -> None:
    t = s3_target("s3:https://cos.ap-shanghai.myqcloud.com/mc-1250000000/mc-ops", "AK", "SK", 512)
    assert t.env == {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK"}
    assert t.options == ("s3.bucket-lookup=dns", "s3.region=ap-shanghai")
    assert t.limit_upload_kib == 512
    # 非 COS 的 S3（如 B2 / MinIO）不加 COS 专用选项
    assert s3_target("s3:https://s3.example.com/b/p", "a", "b", 0).options == ()


def test_smb_target_builds_rclone_env_without_password_in_repo() -> None:
    t = smb_target("192.168.31.10", "/备份/", "mc-ops-restic", "mi", "OBSCURED", "/x/rclone", 256)
    assert t.repo == "rclone:mcopsnas:备份/mc-ops-restic"
    assert t.env == {
        "RCLONE_CONFIG_MCOPSNAS_TYPE": "smb",
        "RCLONE_CONFIG_MCOPSNAS_HOST": "192.168.31.10",
        "RCLONE_CONFIG_MCOPSNAS_USER": "mi",
        "RCLONE_CONFIG_MCOPSNAS_PASS": "OBSCURED",
    }
    assert t.options == ("rclone.program=/x/rclone",)
    # 匿名共享：不带账号密码
    anon = smb_target("nas.local", "share", "", "", "", "rclone", 0)
    assert anon.repo == "rclone:mcopsnas:share" and "RCLONE_CONFIG_MCOPSNAS_USER" not in anon.env


def test_env_isolates_target_credentials(tmp_path: Path) -> None:
    r = Restic(RESTIC, str(tmp_path / "local"), "pw", tmp_path)
    t = OffsiteTarget("T", "s3:https://x/b", {"AWS_ACCESS_KEY_ID": "AK"})
    env = r._env(t)
    assert env["RESTIC_REPOSITORY"] == "s3:https://x/b"
    assert env["RESTIC_FROM_REPOSITORY"] == str(tmp_path / "local")
    assert env["RESTIC_FROM_PASSWORD"] == env["RESTIC_PASSWORD"] == "pw"
    assert "AWS_ACCESS_KEY_ID" not in r._env()  # 本地操作不带异地凭据


async def _cat_config(repo: str) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        RESTIC, "-r", repo, "cat", "config",
        env={**{k: v for k, v in os.environ.items() if not k.startswith("RESTIC_")}, "RESTIC_PASSWORD": "pw"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await proc.communicate()
    return json.loads(out)  # type: ignore[no-any-return]


@needs_restic
async def test_copy_to_initializes_with_same_chunker_and_copies_incrementally(
    tmp_path: Path,
) -> None:
    g = make_game(tmp_path)
    local = Restic(RESTIC, str(tmp_path / "local"), "pw", g, policy=RetentionPolicy(10, 0, 0, 0))
    await local.ensure_repo()
    await local.backup(tags=["t"])
    remote = OffsiteTarget("本地测试目标", str(tmp_path / "remote"), limit_upload_kib=100000)

    first = await local.copy_to(remote, prune=False)
    assert first == {"initialized": True, "copied": 1, "forgot": 0, "pruned": False}
    # 分块参数一致，去重才有效
    a, b = await _cat_config(str(tmp_path / "local")), await _cat_config(str(tmp_path / "remote"))
    assert a["chunker_polynomial"] == b["chunker_polynomial"]

    again = await local.copy_to(remote, prune=True)
    assert again["initialized"] is False and again["copied"] == 0  # 已有的快照不重复复制

    (g / "world" / "level.dat").write_bytes(b"changed")
    await local.backup(tags=["t"])
    third = await local.copy_to(remote, prune=False)
    assert third["copied"] == 1


@needs_restic
async def test_copy_to_unreachable_target_raises(tmp_path: Path) -> None:
    g = make_game(tmp_path)
    local = Restic(RESTIC, str(tmp_path / "local"), "pw", g, timeout_s=60)
    await local.ensure_repo()
    await local.backup(tags=["t"])
    bad = OffsiteTarget("坏目标", "rclone:nope:x", options=("rclone.program=/nonexistent/rclone",))
    with pytest.raises(ResticError):
        await local.copy_to(bad, prune=False)


class _FakeRestic:
    def __init__(self, fail: set[str]) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def copy_to(self, t: OffsiteTarget, prune: bool) -> dict[str, Any]:
        self.calls.append(t.name)
        if t.name in self.fail:
            raise ResticError("连接被拒绝")
        return {"initialized": False, "copied": 2, "forgot": 1, "pruned": prune}


async def test_offsite_job_one_target_fails_others_still_run(ops_env: Env) -> None:
    ops = ops_env.ops
    fake = _FakeRestic({"NAS"})
    ops.restic = fake  # type: ignore[assignment]

    async def targets() -> list[OffsiteTarget]:
        return [OffsiteTarget("NAS", "rclone:x:y"), OffsiteTarget("COS", "s3:https://x/b")]

    ops.offsite_targets = targets
    run_id = await ops.offsite_jobs.run("offsite_copy", "test", ops.offsite_copy)
    (h,) = [x for x in await ops.offsite_jobs.history() if x["id"] == run_id]
    assert h["status"] == "failed" and "NAS 复制失败" in h["log"]
    assert "COS：复制 2 个快照" in h["log"]  # NAS 失败不影响 COS
    assert fake.calls == ["NAS", "COS"]
    assert ops.alerts == [("backup_offsite_failed", "异地备份失败：NAS")]  # type: ignore[attr-defined]

    # 异地任务用自己的锁：跑着的时候定时重启 / Mod 操作不被挡
    assert ops.jobs.running is None


async def test_offsite_job_misconfig_fails_with_alert(ops_env: Env) -> None:
    ops = ops_env.ops
    ops.restic = _FakeRestic(set())  # type: ignore[assignment]

    async def targets() -> list[OffsiteTarget]:
        raise ResticError("已填对象存储地址，但「机密」里还没填 SecretId / SecretKey")

    ops.offsite_targets = targets
    run_id = await ops.offsite_jobs.run("offsite_copy", "test", ops.offsite_copy)
    (h,) = [x for x in await ops.offsite_jobs.history() if x["id"] == run_id]
    assert h["status"] == "failed" and "SecretKey" in h["log"]
    assert ops.alerts == [("backup_offsite_failed", "异地备份失败：配置")]  # type: ignore[attr-defined]
