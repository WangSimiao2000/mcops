"""P4：主机资源采样、首次安装写文件、安装包解包校验、诊断接口。"""

from __future__ import annotations

import asyncio
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from mcops import appsetup, gameprops
from mcops import gameversions as gv
from mcops.auth import hash_password
from mcops.config import load_secrets
from mcops.hoststats import HostStats


async def test_host_stats_sample_and_history(tmp_path: Path) -> None:
    async def pid() -> int | None:
        import os

        return os.getpid()  # 用测试进程自己当"MC 进程"

    h = HostStats({"游戏目录": tmp_path / "game", "备份": tmp_path}, pid)
    await h.sample()
    await asyncio.sleep(0.2)
    s = await h.sample()
    assert s["mem"]["total"] > 0 and 0 <= s["mem"]["used"] <= s["mem"]["total"]
    assert s["cpu"]["cores"] >= 1 and s["cpu"]["pct"] is not None
    assert len(s["disks"]) == 1 and sorted(s["disks"][0]["labels"]) == [
        "备份",
        "游戏目录",
    ]  # 同一文件系统合并
    assert s["mc"]["rss"] > 0 and s["net"] is not None
    j = h.to_json()
    assert len(j["history"]) == 2 and j["current"] is s


def _secrets(tmp: Path) -> Path:
    p = tmp / "secrets.yaml"
    p.write_text(yaml.safe_dump({"admin_password_hash": hash_password("x" * 12)}))
    p.chmod(0o600)
    return p


def test_write_fresh_files(tmp_path: Path) -> None:
    game, sec = tmp_path / "game", _secrets(tmp_path)
    fs = appsetup.FreshSettings(motd="我的服务器", max_players=10, difficulty="hard", port=25570)
    pw = appsetup.write_fresh_files(game, fs, 25575, sec)
    props = gameprops.parse((game / "server.properties").read_text())
    assert (
        props["enable-rcon"] == "true"
        and props["rcon.password"] == pw
        and props["rcon.port"] == "25575"
    )
    assert (
        props["server-port"] == "25570"
        and props["difficulty"] == "hard"
        and props["motd"] == "我的服务器"
    )
    assert props["online-mode"] == "false" and props["white-list"] == "true"
    assert "eula=true" in (game / "eula.txt").read_text()
    assert (game / "server.properties").stat().st_mode & 0o777 == 0o600
    s = load_secrets(sec)
    assert s.rcon_password is not None and s.rcon_password.get_secret_value() == pw
    with pytest.raises(gv.UpgradeError, match="不是一个空目录"):
        appsetup.write_fresh_files(game, fs, 25575, sec)


@pytest.mark.parametrize("bad", [{"motd": "a\nb"}, {"difficulty": "extreme"}, {"port": 0}])
def test_write_fresh_files_rejects(tmp_path: Path, bad: dict[str, Any]) -> None:
    with pytest.raises(gv.UpgradeError):
        appsetup.write_fresh_files(
            tmp_path / "g", appsetup.FreshSettings(**bad), 25575, _secrets(tmp_path)
        )
    assert not (tmp_path / "g" / "server.properties").exists()


def _bundle(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


GOOD = {
    "REVISION": b"abc1234\n",
    "requirements.txt": b"",
    "deploy/install.sh": b"#!/bin/bash\n",
    "web-dist/index.html": b"<html>",
}


def test_unpack_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appsetup, "bundles_dir", lambda: tmp_path / "bundles")
    assert appsetup.unpack_bundle(_bundle(GOOD)) == "abc1234"
    assert (tmp_path / "bundles" / "abc1234" / "deploy" / "install.sh").exists()
    assert appsetup.list_bundles("abc1234")[0] == {
        "revision": "abc1234", "current": True,
        "installed_at": appsetup.list_bundles(None)[0]["installed_at"],
    }  # fmt: skip
    with pytest.raises(gv.UpgradeError, match="缺少"):
        appsetup.unpack_bundle(_bundle({"REVISION": b"abc1234"}))
    with pytest.raises(gv.UpgradeError, match="版本号"):
        appsetup.unpack_bundle(_bundle({**GOOD, "REVISION": b"../../x"}))
    with pytest.raises(gv.UpgradeError, match=r"tar\.gz"):
        appsetup.unpack_bundle(b"not a tarball")


def test_unpack_bundle_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appsetup, "bundles_dir", lambda: tmp_path / "bundles")
    with pytest.raises(tarfile.TarError):  # filter="data" 拒绝 ../ 成员
        appsetup.unpack_bundle(_bundle({**GOOD, "../../escape.txt": b"x"}))
    assert not (tmp_path / "escape.txt").exists()


async def test_schedule_install_builds_systemd_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(appsetup, "bundles_dir", lambda: tmp_path / "bundles")
    appsetup.unpack_bundle(_bundle(GOOD))
    got: list[list[str]] = []

    async def runner(argv: list[str]) -> None:
        got.append(argv)

    await appsetup.schedule_install("abc1234", "mcops-agent.service", runner)
    argv = got[0]
    assert argv[:3] == ["systemd-run", "--user", "--collect"] and "--on-active=2" in argv
    assert "install.sh" in argv[-1] and 'systemctl --user restart "mcops-agent.service"' in argv[-1]
    with pytest.raises(gv.UpgradeError, match="找不到"):
        await appsetup.schedule_install("deadbee", "x", runner)


@pytest.mark.parametrize(
    ("rev", "ok"),
    [
        ("abc1234", True),
        ("v0.1.0", True),
        ("v0.1.0-3-gabc1234", True),
        ("v1.2.3-rc.1", True),
        ("../abc1234", False),
        ("v0.1.0/../x", False),
        ("v0.1", False),
        ("", False),
    ],
)
def test_bundle_revision_accepts_hash_and_tag(rev: str, ok: bool) -> None:
    from mcops.appsetup import REV_RE

    assert bool(REV_RE.match(rev)) is ok
