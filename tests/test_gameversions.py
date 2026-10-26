"""P3：游戏版本预检 / 准备（试启动）/ 替换与回滚 / Java 安装。全部用假网络与假进程。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from mcops import gameversions as gv
from mcops.mods import _HashCache
from tests.test_ops import Env, _running

OLD_JAR = "fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar"
NEW = gv.Target("1.21.12", "0.19.3", "1.1.0")


def jar(mod_id: str, version: str = "1") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps({"id": mod_id, "version": version}))
    return buf.getvalue()


VANILLA = b"vanilla-server" * 100
LAUNCHER = b"PK" + b"\x00" * 20_000
NEWMOD = jar("lithium", "2")


class FakeNet:
    def __init__(self, mod_compatible: bool = True, java_major: int = 21) -> None:
        self.mod_compatible = mod_compatible
        self.java_major = java_major

    async def http(self, method: str, url: str, body: Any) -> Any:
        if url.endswith("/versions/game"):
            return [
                {"version": "1.21.12", "stable": True},
                {"version": "26.1-snapshot", "stable": False},
            ]
        if url.endswith("/versions/loader"):
            return [{"version": "0.19.3", "stable": True}]
        if url.endswith("/versions/installer"):
            return [{"version": "1.1.0", "stable": True}]
        if url == gv.MOJANG_MANIFEST:
            return {
                "versions": [
                    {"id": "1.21.12", "url": "https://piston-meta.mojang.com/v1/1.21.12.json"}
                ]
            }
        if url.startswith("https://piston-meta.mojang.com/v1/"):
            return {
                "javaVersion": {"majorVersion": self.java_major},
                "downloads": {"server": {"url": "https://piston-data.mojang.com/v1/server.jar",
                                         "sha1": hashlib.sha1(VANILLA).hexdigest(), "size": len(VANILLA)}},
            }  # fmt: skip
        if "version_files/update" in url:
            sha = body["hashes"][0]
            if not self.mod_compatible:
                return {}
            return {sha: {"project_id": "P", "id": "v2", "version_number": "2.0", "files": [
                {"primary": True, "filename": "lithium-2.0.jar", "url": "https://cdn.modrinth.com/lithium-2.0.jar",
                 "hashes": {"sha512": hashlib.sha512(NEWMOD).hexdigest()}, "size": len(NEWMOD)}]}}  # fmt: skip
        if url.startswith(f"{gv.FABRIC_META}/versions/loader/"):
            return [{"loader": {"version": "0.19.3", "stable": True}}]
        raise AssertionError(url)

    async def download(self, url: str, dest: Path, max_bytes: int) -> int:
        data = {"server.jar": VANILLA, "jar": LAUNCHER, "lithium-2.0.jar": NEWMOD}[
            url.rsplit("/", 1)[-1]
        ]
        dest.write_bytes(data)
        return len(data)


def runner_factory(prewarm_ok: bool = True, java_major: int = 21) -> Any:
    async def run(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
        if argv[1:] == ["-version"]:
            return 0, f'openjdk version "{java_major}.0.5" 2026-01-01'
        if not prewarm_ok:
            return 1, "Exception in thread main"
        assert (cwd / "eula.txt").read_text() == "eula=false\n"
        assert not (cwd / "mods").exists()  # 试启动时不能加载新 mod
        (cwd / "libraries").mkdir()
        (cwd / "versions").mkdir()
        return 0, "You need to agree to the EULA in order to run the server."

    return run


def game(tmp: Path) -> Path:
    g = tmp / "game"
    (g / "mods").mkdir(parents=True)
    (g / "world").mkdir()
    (g / "world" / "level.dat").write_bytes(b"old-world")
    (g / OLD_JAR).write_bytes(b"old")
    (g / "libraries").mkdir()
    (g / "libraries" / "old.lib").write_bytes(b"x")
    (g / "mods" / "lithium-1.0.jar").write_bytes(jar("lithium"))
    return g


async def plan(g: Path, net: FakeNet, **kw: Any) -> dict[str, Any]:
    return await gv.precheck(net.http, g, _HashCache(), "java", NEW, systemd=kw.get("systemd", False),
                             manage_unit=kw.get("manage_unit", False), runner=runner_factory())  # fmt: skip


async def test_versions_list() -> None:
    v = await gv.list_versions(FakeNet().http)
    assert v["installer"] == "1.1.0" and v["games"][0] == {"version": "1.21.12", "stable": True}


async def test_precheck_reports_mods_java_and_blockers(tmp_path: Path) -> None:
    g = game(tmp_path)
    p = await plan(g, FakeNet())
    assert p["current"]["mc"] == "1.21.11" and p["target"]["jar"] == gv.jar_name(NEW)
    assert p["java"] == {"required": 21, "current": 21, "needs_install": False}
    assert p["mods"][0]["status"] == "update" and p["mods"][0]["new_file"] == "lithium-2.0.jar"
    assert p["blockers"] == []
    p2 = await plan(g, FakeNet(), systemd=True, manage_unit=False)
    assert "单元文件" in p2["blockers"][0]
    p3 = await gv.precheck(FakeNet(java_major=25).http, g, _HashCache(), "java", NEW,
                           systemd=False, manage_unit=False, runner=runner_factory())  # fmt: skip
    assert p3["java"]["needs_install"] is True


async def test_precheck_blocks_downgrade(tmp_path: Path) -> None:
    g = game(tmp_path)
    (g / OLD_JAR).rename(g / "fabric-server-mc.1.21.13-loader.0.19.2-launcher.1.0.3.jar")
    p = await plan(g, FakeNet())
    assert any("降级" in b for b in p["blockers"])


async def test_prepare_downloads_verifies_and_prewarms(tmp_path: Path) -> None:
    g, data = game(tmp_path), tmp_path / "data"
    net = FakeNet()
    p = await plan(g, net)
    notes: list[str] = []
    prep = await gv.prepare(net.http, net.download, g, data, p, disable_unknown=False,
                            runner=runner_factory(), note=notes.append)  # fmt: skip
    assert (prep.staging / prep.jar).read_bytes() == LAUNCHER
    assert (prep.staging / ".fabric" / "server" / "1.21.12-server.jar").read_bytes() == VANILLA
    assert (prep.staging / "_mods" / "lithium-2.0.jar").exists() and (
        prep.staging / "libraries"
    ).is_dir()
    assert any("试启动通过" in n for n in notes)
    # 游戏目录完全没动
    assert (g / OLD_JAR).exists() and (g / "libraries" / "old.lib").exists()


async def test_prepare_fails_cleanly_when_prewarm_fails(tmp_path: Path) -> None:
    g = game(tmp_path)
    net = FakeNet()
    p = await plan(g, net)
    with pytest.raises(gv.UpgradeError, match="试启动失败"):
        await gv.prepare(net.http, net.download, g, tmp_path / "d", p, disable_unknown=False,
                         runner=runner_factory(prewarm_ok=False))  # fmt: skip
    assert (g / OLD_JAR).exists()


async def test_prepare_rejects_bad_vanilla_hash(tmp_path: Path) -> None:
    g = game(tmp_path)
    net = FakeNet()
    p = await plan(g, net)

    async def bad_download(url: str, dest: Path, mx: int) -> int:
        n = await net.download(url, dest, mx)
        if url.endswith("server.jar"):
            dest.write_bytes(b"tampered")
        return n

    with pytest.raises(gv.UpgradeError, match="sha1"):
        await gv.prepare(net.http, bad_download, g, tmp_path / "d", p, disable_unknown=False,
                         runner=runner_factory())  # fmt: skip


async def test_swap_in_and_out_roundtrip(tmp_path: Path) -> None:
    g = game(tmp_path)
    net = FakeNet()
    (g / "mods" / "private.jar").write_bytes(jar("private"))
    p = await plan(g, net)
    p["mods"].append({"file": "private.jar", "status": "unknown"})
    prep = await gv.prepare(net.http, net.download, g, tmp_path / "d", p, disable_unknown=True,
                            runner=runner_factory())  # fmt: skip
    rec = gv.swap_in(g, prep, tmp_path / "swap")
    assert gv.current_jar(g) == gv.jar_name(NEW) and not (g / OLD_JAR).exists()
    assert sorted(f.name for f in (g / "mods").iterdir()) == ["lithium-2.0.jar"]
    assert not (g / "libraries" / "old.lib").exists()
    gv.swap_out(g, rec)
    assert gv.current_jar(g) == OLD_JAR
    assert sorted(f.name for f in (g / "mods").iterdir()) == ["lithium-1.0.jar", "private.jar"]
    assert (g / "libraries" / "old.lib").exists() and not (g / ".fabric").exists()


def test_restore_world(tmp_path: Path) -> None:
    g = game(tmp_path)
    tar = tmp_path / "b.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        tf.add(g / "world", arcname="world")
    (g / "world" / "level.dat").write_bytes(b"converted-by-new-version")
    gv.restore_world(g, tar, tmp_path / "q")
    assert (g / "world" / "level.dat").read_bytes() == b"old-world"
    assert (tmp_path / "q" / "world" / "level.dat").read_bytes() == b"converted-by-new-version"


async def test_install_java_verifies_and_extracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in [("jdk-25-jre/bin/java", b"#!/bin/sh\n"), ("jdk-25-jre/release", b"x")]:
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o755
            tf.addfile(ti, io.BytesIO(data))
    blob = buf.getvalue()

    async def http(method: str, url: str, body: Any) -> Any:
        assert "/assets/latest/25/hotspot" in url
        return [{"release_name": "jdk-25+36", "binary": {"package": {
            "name": "OpenJDK25U-jre_x64_linux_hotspot_25_36.tar.gz", "size": len(blob),
            "checksum": hashlib.sha256(blob).hexdigest(), "link": "https://github.com/x.tar.gz"}}}]  # fmt: skip

    urls: list[str] = []

    async def download(url: str, dest: Path, mx: int) -> int:
        urls.append(url)
        dest.write_bytes(blob)
        return len(blob)

    async def fake_major(java_bin: str, runner: Any = None) -> int:
        return 25

    monkeypatch.setattr(gv, "java_major", fake_major)
    java = await gv.install_java(http, download, tmp_path, 25, "tuna")
    assert urls[0].startswith("https://mirrors.tuna.tsinghua.edu.cn/Adoptium/25/jre/x64/linux/")
    assert Path(java).is_file() and gv.managed_javas(tmp_path)[0]["major"] == 25
    again = await gv.install_java(http, download, tmp_path, 25)
    assert again == java and len(urls) == 1  # 已安装不重复下载


# ---- 运维任务：完整升级与失败回滚（复用 test_ops 的 Env） ---------------------------


async def _prep_for(env: Env, tmp_path: Path) -> gv.Prepared:
    g = env.ops.backup_cfg.game_dir
    (g / OLD_JAR).write_bytes(b"old")
    (g / "mods" / "lithium-1.0.jar").write_bytes(jar("lithium"))
    net = FakeNet()
    p = await gv.precheck(net.http, g, _HashCache(), "java", NEW, systemd=False, manage_unit=False,
                          runner=runner_factory())  # fmt: skip
    return await gv.prepare(net.http, net.download, g, tmp_path / "d", p, disable_unknown=False,
                            runner=runner_factory())  # fmt: skip


async def _cycle(env: Env, ready: bool) -> None:
    """模拟进程：收到 stop 就退出；开服时 ready=True 则就绪，否则进程直接退出。"""
    while "stop" not in env.rcon.commands:
        await asyncio.sleep(0.02)
    env.backend.alive = False
    while env.backend.started < 2:
        await asyncio.sleep(0.02)
    if ready:
        env.srv._ready.set()
    else:
        env.backend.alive = False  # 新版本一启动就崩
        while env.backend.started < 3:  # 等回滚后的旧版本开服
            await asyncio.sleep(0.02)
        env.srv._ready.set()


async def test_game_upgrade_job_success(ops_env: Env, tmp_path: Path) -> None:
    env = ops_env
    prep = await _prep_for(env, tmp_path)
    await _running(env)
    t = asyncio.create_task(_cycle(env, ready=True))
    saved: list[str] = []

    async def on_java(j: str) -> None:
        saved.append(j)

    await env.ops.jobs.run("game_upgrade", "test", env.ops.game_upgrade(prep, on_java))
    await t
    (h,) = await env.ops.jobs.history()
    assert h["status"] == "ok", h["log"]
    g = env.ops.backup_cfg.game_dir
    assert gv.current_jar(g) == gv.jar_name(NEW) and env.srv.cfg.jar == gv.jar_name(NEW)
    assert (g / "mods" / "lithium-2.0.jar").exists()
    assert any("升级到 1.21.12" in c for c in env.rcon.commands)  # 倒计时广播


async def test_game_upgrade_job_rolls_back(ops_env: Env, tmp_path: Path) -> None:
    env = ops_env
    prep = await _prep_for(env, tmp_path)
    g = env.ops.backup_cfg.game_dir
    (g / "world" / "level.dat").write_bytes(b"old-world")
    await _running(env)
    t = asyncio.create_task(_cycle(env, ready=False))
    with pytest.raises(RuntimeError, match="已回滚"):
        await env.ops.game_upgrade(prep)(lambda _m: None)
    await t
    assert gv.current_jar(g) == OLD_JAR and env.srv.cfg.jar == "s.jar"
    assert (g / "world" / "level.dat").read_bytes() == b"old-world"
    assert (
        not (g / "mods" / "lithium-2.0.jar").exists() and (g / "mods" / "lithium-1.0.jar").exists()
    )
    assert env.backend.started == 3  # 原来 1 次 + 新版本 1 次 + 回滚后旧版本 1 次


async def test_game_upgrade_rolls_back_world_from_restic(ops_env: Env, tmp_path: Path) -> None:
    """开了 restic：升级前拍停服快照，新版本开不起来时从快照恢复世界（新版本已经改过的世界移走）。"""
    import shutil

    from mcops.restic import Restic

    restic = shutil.which("restic") or str(Path.home() / ".local/bin/restic")
    if not Path(restic).exists():
        pytest.skip("restic 未安装")
    env = ops_env
    g = env.ops.backup_cfg.game_dir
    env.ops.restic = Restic(restic, str(tmp_path / "repo"), "pw", g, timeout_s=120)
    prep = await _prep_for(env, tmp_path)
    (g / "world" / "level.dat").write_bytes(b"old-world")
    await _running(env)

    async def cycle() -> None:
        while "stop" not in env.rcon.commands:
            await asyncio.sleep(0.02)
        env.backend.alive = False
        while env.backend.started < 2:
            await asyncio.sleep(0.02)
        (g / "world" / "level.dat").write_bytes(b"converted-by-new-version")
        env.backend.alive = False  # 新版本一启动就崩
        while env.backend.started < 3:
            await asyncio.sleep(0.02)
        env.srv._ready.set()

    t = asyncio.create_task(cycle())
    with pytest.raises(RuntimeError, match="已回滚"):
        await env.ops.game_upgrade(prep)(lambda _m: None)
    await t
    assert (g / "world" / "level.dat").read_bytes() == b"old-world"
    assert not any(env.ops.backup_cfg.backup_dir.glob("mc-backup-*"))  # 没打 tar
    (snap,) = await env.ops.restic.snapshots()
    assert set(snap["tags"]) == {"stopped", "pre-upgrade"}
    failed = next((g / ".mcops-backup").glob("upgrade-*/failed/world/level.dat"))
    assert failed.read_bytes() == b"converted-by-new-version"
