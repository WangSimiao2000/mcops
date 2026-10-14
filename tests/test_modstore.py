"""P2：Modrinth 搜索/依赖解析/安装、上传、mrpack、config 编辑。全部用假 HTTP，不触网。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from mcops import modstore
from mcops.db import open_db
from mcops.mods import ModError, _HashCache


def jar_bytes(mod_id: str, deps: dict[str, str] | None = None, version: str = "1.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {"schemaVersion": 1, "id": mod_id, "version": version, "depends": deps or {}}
            ),
        )
    return buf.getvalue()


def sha(b: bytes) -> str:
    return hashlib.sha512(b).hexdigest()


class FakeModrinth:
    """项目 lithium(依赖 fabric-api)、fabric-api、clientmod(纯客户端)、nover(没有 1.21.11 版本)。"""

    def __init__(self) -> None:
        self.blobs = {
            "lithium.jar": jar_bytes("lithium", {"fabric-api": "*"}),
            "fabric-api.jar": jar_bytes("fabric-api"),
        }
        self.calls: list[str] = []

    def version(self, pid: str, vid: str, fname: str, deps: list[dict[str, Any]]) -> dict[str, Any]:
        b = self.blobs[fname]
        return {
            "id": vid, "project_id": pid, "version_number": "1.0", "version_type": "release",
            "game_versions": ["1.21.11"], "loaders": ["fabric"], "dependencies": deps,
            "files": [{"primary": True, "filename": fname, "url": f"https://cdn.modrinth.com/{fname}",
                       "hashes": {"sha512": sha(b)}, "size": len(b)}],
        }  # fmt: skip

    async def http(self, method: str, url: str, body: dict[str, Any] | None) -> Any:
        self.calls.append(url)
        path = url.removeprefix(modstore.API)
        if path.startswith("/search"):
            assert "versions%3A1.21.11" in path or "versions:1.21.11" in path
            return {"hits": [{"project_id": "L", "slug": "lithium", "title": "Lithium", "server_side": "optional"}],
                    "total_hits": 1}  # fmt: skip
        if path == "/version_files":
            return {}  # 没有已装的
        projects = {
            "L": {"id": "L", "title": "Lithium", "server_side": "optional"},
            "lithium": {"id": "L", "title": "Lithium", "server_side": "optional"},
            "F": {"id": "F", "title": "Fabric API", "server_side": "required"},
            "C": {"id": "C", "title": "ClientMod", "server_side": "unsupported"},
            "N": {"id": "N", "title": "NoVer", "server_side": "required"},
        }
        if path.startswith("/project/") and "/version" not in path:
            return projects[path.split("/")[2]]
        if path.startswith("/project/L/version"):
            return [self.version("L", "lv1", "lithium.jar", [{"project_id": "F", "dependency_type": "required"},
                                                            {"project_id": "C", "dependency_type": "optional"}])]  # fmt: skip
        if path.startswith("/project/F/version"):
            return [self.version("F", "fv1", "fabric-api.jar", [])]
        if path.startswith("/project/N/version"):
            return []
        raise AssertionError(f"unexpected {url}")

    async def download(self, url: str, dest: Path, max_bytes: int) -> int:
        data = self.blobs[url.rsplit("/", 1)[-1]]
        dest.write_bytes(data)
        return len(data)


@pytest.fixture
async def env(tmp_path: Path) -> Any:
    game = tmp_path / "game"
    (game / "mods").mkdir(parents=True)
    (game / "config").mkdir()
    engine = await open_db(tmp_path / "t.db")
    yield game, engine, FakeModrinth()
    await engine.dispose()


async def test_search_filters_by_version(env: Any) -> None:
    _game, _e, fm = env
    r = await modstore.search(fm.http, "lith", "1.21.11")
    assert r["hits"][0]["slug"] == "lithium" and r["total"] == 1


async def test_plan_resolves_required_deps_only(env: Any) -> None:
    game, _e, fm = env
    plan = await modstore.plan_install(
        fm.http, game, _HashCache(), "1.21.11", [{"project_id": "lithium"}]
    )
    by = {i["title"]: i for i in plan["items"]}
    assert by["Lithium"]["status"] == "install" and by["Lithium"]["reason"] == "requested"
    assert by["Fabric API"]["status"] == "install" and by["Fabric API"]["required_by"] == [
        "Lithium"
    ]
    assert "ClientMod" not in by  # optional 依赖不装
    assert plan["install_count"] == 2


async def test_plan_reports_client_only_and_unavailable(env: Any) -> None:
    game, _e, fm = env
    plan = await modstore.plan_install(fm.http, game, _HashCache(), "1.21.11",
                                       [{"project_id": "C"}, {"project_id": "N"}])  # fmt: skip
    st = {i["title"]: i["status"] for i in plan["items"]}
    assert st == {"ClientMod": "client_only", "NoVer": "unavailable"}
    assert plan["install_count"] == 0 and len(plan["warnings"]) == 2


async def test_install_downloads_verifies_and_marks_pending(env: Any) -> None:
    game, engine, fm = env
    plan = await modstore.plan_install(
        fm.http, game, _HashCache(), "1.21.11", [{"project_id": "L"}]
    )
    r = await modstore.install_items(engine, game, fm.download, plan["items"])
    assert sorted(r["installed"]) == ["fabric-api.jar", "lithium.jar"]
    assert (game / "mods" / "lithium.jar").read_bytes() == fm.blobs["lithium.jar"]
    assert not list((game / "mods").glob("*.part"))


async def test_install_rolls_back_all_on_bad_hash(env: Any) -> None:
    game, engine, fm = env
    plan = await modstore.plan_install(
        fm.http, game, _HashCache(), "1.21.11", [{"project_id": "L"}]
    )
    fm.blobs["fabric-api.jar"] = b"tampered"  # 第二个文件被篡改
    with pytest.raises(ModError, match=r"大小不符|sha512"):
        await modstore.install_items(engine, game, fm.download, plan["items"])
    assert list((game / "mods").iterdir()) == []  # 第一个已下好的也撤回了


async def test_install_rejects_untrusted_host(env: Any) -> None:
    game, engine, fm = env
    items = [{"status": "install", "filename": "x.jar", "url": "https://evil.example/x.jar",
              "sha512": "0", "size": 1}]  # fmt: skip
    with pytest.raises(ModError, match="允许范围"):
        await modstore.install_items(engine, game, fm.download, items)


async def test_upload_validates_jar(env: Any) -> None:
    game, engine, _fm = env
    r = await modstore.upload_jar(engine, game, _HashCache(), "my.jar",
                                  jar_bytes("mymod", {"minecraft": "1.20.1"}), "1.21.11")  # fmt: skip
    assert r["id"] == "mymod" and r["warnings"]  # 版本不匹配给警告但允许
    with pytest.raises(ModError, match="不是 Fabric"):
        await modstore.upload_jar(engine, game, _HashCache(), "bad.jar", b"PK not a jar", None)
    with pytest.raises(ModError, match="已安装同一个"):
        await modstore.upload_jar(engine, game, _HashCache(), "my2.jar", jar_bytes("mymod"), None)
    for bad in ["../x.jar", "x.zip", ".hidden.jar", "a/b.jar"]:
        with pytest.raises(ModError, match="非法"):
            await modstore.upload_jar(engine, game, _HashCache(), bad, jar_bytes("z"), None)
    assert sorted(p.name for p in (game / "mods").iterdir()) == ["my.jar"]


def mrpack(
    files: list[dict[str, Any]], overrides: dict[str, bytes], deps: dict[str, str] | None = None
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps({
            "formatVersion": 1, "game": "minecraft", "versionId": "1", "name": "Pack",
            "files": files, "dependencies": deps or {"minecraft": "1.21.11", "fabric-loader": "0.19.2"},
        }))  # fmt: skip
        for n, b in overrides.items():
            zf.writestr(n, b)
    return buf.getvalue()


async def test_mrpack_plan_and_apply(env: Any) -> None:
    game, engine, fm = env
    (game / "config" / "a.json").write_text('{"old": 1}')
    (game / "mods" / "lithium.jar").write_bytes(b"old")
    lb = fm.blobs["lithium.jar"]
    data = mrpack(
        [
            {"path": "mods/lithium.jar", "hashes": {"sha512": sha(lb)}, "fileSize": len(lb),
             "downloads": ["https://cdn.modrinth.com/lithium.jar"], "env": {"server": "required"}},
            {"path": "mods/shader.jar", "hashes": {"sha512": "x"}, "fileSize": 1,
             "downloads": ["https://cdn.modrinth.com/s.jar"], "env": {"server": "unsupported"}},
            {"path": "mods/evil.jar", "hashes": {"sha512": "x"}, "fileSize": 1,
             "downloads": ["https://evil.example/e.jar"]},
            {"path": "resourcepacks/r.zip", "hashes": {"sha512": "x"}, "fileSize": 1,
             "downloads": ["https://cdn.modrinth.com/r.zip"]},
        ],
        {"overrides/config/a.json": b'{"new": 2}', "server-overrides/config/b.toml": b"x = 1",
         "overrides/world/level.dat": b"nope"},
    )  # fmt: skip
    plan = modstore.read_mrpack(data, "1.21.11", "0.19.2")
    assert [f["path"] for f in plan["files"]] == ["mods/lithium.jar"]
    reasons = {s["path"]: s["reason"] for s in plan["skipped"]}
    assert reasons["mods/shader.jar"] == "仅客户端" and "下载地址" in reasons["mods/evil.jar"]
    assert "overrides/world/level.dat" in reasons  # 不碰世界
    r = await modstore.apply_mrpack(engine, game, fm.download, data, plan)
    assert (game / "mods" / "lithium.jar").read_bytes() == lb
    assert (game / "config" / "a.json").read_text() == '{"new": 2}'
    assert (game / "config" / "b.toml").exists()
    assert "mods/lithium.jar" in r["replaced"] and "config/a.json" in r["replaced"]
    assert list((game / "mods" / "disabled").glob("lithium.jar.replaced-*"))  # 旧 jar 可回退
    assert not (game / "world").exists()


@pytest.mark.parametrize("bad", ["../../etc/x", "/abs/path", "mods/../../x.jar"])
def test_mrpack_rejects_traversal(bad: str) -> None:
    data = mrpack(
        [{"path": bad, "hashes": {"sha512": "x"}, "downloads": ["https://cdn.modrinth.com/x"]}], {}
    )
    with pytest.raises(ModError, match="非法路径"):
        modstore.read_mrpack(data, None, None)


def test_mrpack_rejects_non_fabric() -> None:
    with pytest.raises(ModError, match="只支持 Fabric"):
        modstore.read_mrpack(mrpack([], {}, {"minecraft": "1.21.11", "forge": "1"}), None, None)


def test_config_editor(tmp_path: Path) -> None:
    game = tmp_path
    (game / "config" / "sub").mkdir(parents=True)
    (game / "config" / "sub" / "x.json").write_text('{"a": 1}')
    (game / "config" / "y.toml").write_text("a = 1")
    (game / "config" / "z.bin").write_bytes(b"\x00")
    (game / "secret.txt").write_text("no")
    (game / "config" / "link.json").symlink_to(game / "secret.txt")
    paths = [f["path"] for f in modstore.list_config_files(game)]
    assert paths == ["sub/x.json", "y.toml"]  # 不列二进制、不跟随符号链接
    f = modstore.read_config_file(game, "sub/x.json")
    r = modstore.write_config_file(game, tmp_path / "bk", "sub/x.json", '{"a": 2}', f["mtime"])
    assert (game / "config" / "sub" / "x.json").read_text() == '{"a": 2}'
    assert Path(r["backup"]).read_text() == '{"a": 1}'
    with pytest.raises(ModError, match="JSON 语法"):
        modstore.write_config_file(game, tmp_path / "bk", "sub/x.json", "{bad", None)
    with pytest.raises(ModError, match="TOML 语法"):
        modstore.write_config_file(game, tmp_path / "bk", "y.toml", "a = = 1", None)
    with pytest.raises(ModError, match="被修改过"):
        modstore.write_config_file(game, tmp_path / "bk", "sub/x.json", "{}", f["mtime"] - 100)
    for bad in ["../secret.txt", "/etc/passwd", "link.json"]:
        with pytest.raises(ModError):
            modstore.write_config_file(game, tmp_path / "bk", bad, "{}", None)
    assert (game / "secret.txt").read_text() == "no"


# ---- API 层 --------------------------------------------------------------------


@pytest.fixture
async def api(tmp_path: Path) -> Any:
    from datetime import UTC

    import httpx

    from mcops import api_mods
    from mcops.api import Services, create_app
    from mcops.auth import Authenticator, hash_password
    from mcops.config import ApiConfig, ServerConfig
    from mcops.events import EventBus
    from mcops.rcon import RconClient
    from mcops.server import ServerController
    from mcops.supervisor import Supervisor
    from tests.fakes import FakeBackend

    game = tmp_path / "game"
    (game / "mods").mkdir(parents=True)
    (game / "config").mkdir()
    (game / "config" / "c.json").write_text('{"a": 1}')
    jar = "fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar"
    (game / jar).write_bytes(b"x")
    engine = await open_db(tmp_path / "t.db")
    cfg = ServerConfig(game_dir=game, jar=jar)
    server = ServerController(
        cfg, FakeBackend(), RconClient("127.0.0.1", 1, ""), EventBus(engine), UTC
    )
    auth = Authenticator(engine, hash_password("pw-pw-pw-pw-pw"), None, 3600)
    svc = Services(ApiConfig(), engine, EventBus(engine), server, auth, Supervisor())
    fm = FakeModrinth()
    svc.mods_http, svc.mods_download = fm.http, fm.download  # type: ignore[attr-defined]
    app = create_app()
    api_mods.register(app)
    app.state.svc = svc
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as c:
        assert (
            await c.post("/api/auth/login", json={"password": "pw-pw-pw-pw-pw"})
        ).status_code == 200
        c.headers["X-CSRF-Token"] = c.cookies["mcops_csrf"]
        yield c, game
    await engine.dispose()


async def test_api_search_plan_install(api: Any) -> None:
    c, _game = api
    r = (await c.get("/api/mods/search", params={"q": "lith"})).json()
    assert r["mc_version"] == "1.21.11" and r["hits"][0]["installed_file"] is None
    plan = (await c.post("/api/mods/install/plan", json={"items": [{"project_id": "L"}]})).json()
    assert plan["install_count"] == 2
    r = await c.post("/api/mods/install", json={"items": [{"project_id": "L"}]})
    assert r.status_code == 200 and sorted(r.json()["installed"]) == [
        "fabric-api.jar",
        "lithium.jar",
    ]
    bad = await c.post("/api/mods/install", json={"items": [{"project_id": "../x"}]})
    assert bad.status_code == 422


async def test_api_upload_config_and_mrpack(api: Any) -> None:
    c, game = api
    r = await c.post("/api/mods/upload", params={"filename": "m.jar"}, content=jar_bytes("m"))
    assert r.status_code == 200 and (game / "mods" / "m.jar").exists()
    assert (
        await c.post("/api/mods/upload", params={"filename": "m.zip"}, content=b"x")
    ).status_code == 400
    f = (await c.get("/api/mods/config-file", params={"path": "c.json"})).json()
    r = await c.put(
        "/api/mods/config-file", json={"path": "c.json", "content": "{bad", "mtime": f["mtime"]}
    )
    assert r.status_code == 400 and "JSON" in r.json()["detail"]
    r = await c.put(
        "/api/mods/config-file", json={"path": "c.json", "content": '{"a": 2}', "mtime": f["mtime"]}
    )
    assert r.status_code == 200
    plan = (
        await c.post(
            "/api/mods/mrpack/plan", content=mrpack([], {"overrides/config/n.json": b"{}"})
        )
    ).json()
    assert plan["overrides"] == ["overrides/config/n.json"]
    assert (
        await c.post("/api/mods/mrpack/apply", json={"token": plan["token"]})
    ).status_code == 200
    assert (game / "config" / "n.json").exists()
    again = await c.post("/api/mods/mrpack/apply", json={"token": plan["token"]})
    assert again.status_code == 409  # token 一次性
