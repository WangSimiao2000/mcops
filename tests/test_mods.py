"""Mod 与升级（M4）测试：元数据解析、健康检查、版本范围、文件操作、更新/回退、路径穿越、observe 409。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcops import mods as m
from mcops.db import open_db

# ---- 工具：生成假 jar --------------------------------------------------------


def make_jar(
    path: Path,
    meta: dict[str, Any] | None,
    *,
    nested: dict[str, dict[str, Any]] | None = None,
    bad: bool = False,
    empty: bool = False,
) -> None:
    """写一个 jar：meta=fabric.mod.json 内容；nested={内嵌文件名: 内嵌 fabric.mod.json}。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if empty:
        path.write_bytes(b"")
        return
    if bad:
        path.write_bytes(b"not a zip file at all")
        return
    with zipfile.ZipFile(path, "w") as zf:
        meta = dict(meta) if meta is not None else None
        if meta is not None and nested:
            # fabric.mod.json 用 jars:[{file: ...}] 声明内嵌 jar
            meta.setdefault("jars", [{"file": fname} for fname in nested])
        if meta is not None:
            zf.writestr("fabric.mod.json", json.dumps(meta))
        for fname, inner_meta in (nested or {}).items():
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as inner:
                inner.writestr("fabric.mod.json", json.dumps(inner_meta))
            zf.writestr(fname, buf.getvalue())


def sha512_of(path: Path) -> str:
    return hashlib.sha512(path.read_bytes()).hexdigest()


@pytest.fixture
def game(tmp_path: Path) -> Path:
    g = tmp_path / "game"
    (g / "mods").mkdir(parents=True)
    (g / "mods" / "disabled").mkdir()
    # 服务端 jar，用于解析 MC / loader 版本
    (g / "fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar").write_bytes(b"x")
    return g


# ---- 元数据解析 + 内嵌 provides ---------------------------------------------


def test_parse_metadata_and_nested_provides(game: Path) -> None:
    make_jar(
        game / "mods" / "fabric-api.jar",
        {
            "id": "fabric-api",
            "name": "Fabric API",
            "version": "0.100.0",
            "description": "x" * 500,
            "authors": ["Alice", {"name": "Bob"}],
            "contact": {"homepage": "https://example.test/fa"},
            "depends": {"minecraft": "1.21.11", "java": ">=21"},
            "environment": "*",
            "provides": ["fabric-rendering-v1"],
        },
        nested={"META-INF/jars/embedded.jar": {"id": "embedded-lib", "version": "1.0"}},
    )
    hashes = m._HashCache()
    mods = m.scan_mods(game, hashes)
    mod = next(x for x in mods if x.id == "fabric-api")
    assert mod.kind == "fabric"
    assert mod.name == "Fabric API"
    assert mod.authors == ["Alice", "Bob"]
    assert mod.homepage == "https://example.test/fa"
    assert len(mod.description) <= 301  # 截断到 300 + 省略号
    assert "fabric-rendering-v1" in mod.provides
    assert "embedded-lib" in mod.provides  # 内嵌 jar 的 id
    assert mod.sha512 == sha512_of(game / "mods" / "fabric-api.jar")


def test_hash_cache_reuses(game: Path) -> None:
    make_jar(game / "mods" / "a.jar", {"id": "a", "version": "1"})
    hashes = m._HashCache()
    mods1 = m.scan_mods(game, hashes)
    assert len(hashes._cache) == 1
    mods2 = m.scan_mods(game, hashes)
    assert mods1[0].sha512 == mods2[0].sha512
    assert len(hashes._cache) == 1  # 未重复计算


def test_bad_and_non_fabric_jars_marked_unknown(game: Path) -> None:
    make_jar(game / "mods" / "broken.jar", None, bad=True)
    make_jar(game / "mods" / "plain.jar", None)  # zip 但无 fabric.mod.json
    hashes = m._HashCache()
    mods = {x.file: x for x in m.scan_mods(game, hashes)}
    assert mods["broken.jar"].kind == "unknown"
    assert mods["plain.jar"].kind == "unknown"


def test_disabled_group(game: Path) -> None:
    make_jar(game / "mods" / "on.jar", {"id": "on", "version": "1"})
    make_jar(game / "mods" / "disabled" / "off.jar", {"id": "off", "version": "1"})
    hashes = m._HashCache()
    mods = {x.file: x for x in m.scan_mods(game, hashes)}
    assert mods["on.jar"].enabled is True
    assert mods["off.jar"].enabled is False


# ---- 健康检查 ----------------------------------------------------------------


def test_health_empty_file(game: Path) -> None:
    make_jar(game / "mods" / "empty.jar", None, empty=True)
    hashes = m._HashCache()
    issues = m.check_health(m.scan_mods(game, hashes), "1.21.11")
    assert any(i["level"] == "error" and "空文件" in i["message"] for i in issues)


def test_health_duplicate_id(game: Path) -> None:
    make_jar(game / "mods" / "a.jar", {"id": "dup", "version": "1"})
    make_jar(game / "mods" / "b.jar", {"id": "dup", "version": "2"})
    hashes = m._HashCache()
    issues = m.check_health(m.scan_mods(game, hashes), "1.21.11")
    assert any(i["level"] == "error" and "重复" in i["message"] for i in issues)


def test_health_missing_dependency(game: Path) -> None:
    make_jar(
        game / "mods" / "needs.jar", {"id": "needs", "version": "1", "depends": {"somelib": "*"}}
    )
    make_jar(game / "mods" / "easyauth.jar", {"id": "easyauth", "version": "1"})
    hashes = m._HashCache()
    issues = m.check_health(m.scan_mods(game, hashes), "1.21.11")
    assert any("somelib" in i["message"] for i in issues)


def test_health_fabric_prefix_dep_satisfied_by_fabric_api(game: Path) -> None:
    make_jar(game / "mods" / "fabric-api.jar", {"id": "fabric-api", "version": "1"})
    make_jar(game / "mods" / "easyauth.jar", {"id": "easyauth", "version": "1"})
    make_jar(
        game / "mods" / "user.jar",
        {"id": "user", "version": "1", "depends": {"fabric-rendering-v1": "*"}},
    )
    hashes = m._HashCache()
    issues = m.check_health(m.scan_mods(game, hashes), "1.21.11")
    # fabric- 前缀依赖在装了 fabric-api 时视为已提供
    assert not any("fabric-rendering-v1" in i["message"] for i in issues)


def test_health_mc_version_mismatch(game: Path) -> None:
    make_jar(
        game / "mods" / "old.jar", {"id": "old", "version": "1", "depends": {"minecraft": "1.20.1"}}
    )
    make_jar(game / "mods" / "easyauth.jar", {"id": "easyauth", "version": "1"})
    hashes = m._HashCache()
    issues = m.check_health(m.scan_mods(game, hashes), "1.21.11")
    assert any("1.20.1" in i["message"] for i in issues)


def test_health_missing_easyauth(game: Path) -> None:
    make_jar(game / "mods" / "a.jar", {"id": "a", "version": "1"})
    hashes = m._HashCache()
    issues = m.check_health(m.scan_mods(game, hashes), "1.21.11")
    assert any("EasyAuth" in i["message"] for i in issues)


# ---- 版本范围匹配 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "mc", "expected"),
    [
        ("1.21.11", "1.21.11", True),
        ("1.21.11", "1.21.10", False),
        (">=1.21", "1.21.11", True),
        (">=1.22", "1.21.11", False),
        ("<=1.21.11", "1.21.11", True),
        (">1.21.10", "1.21.11", True),
        ("<1.21.11", "1.21.11", False),
        ("~1.21.11", "1.21.15", True),
        ("~1.21.11", "1.22.0", False),
        ("^1.21.11", "1.25.0", True),
        ("^1.21.11", "2.0.0", False),
        ("1.21.x", "1.21.11", True),
        ("1.21.x", "1.22.0", False),
        (">=1.21 <1.22", "1.21.11", True),
        (">=1.21 <1.21.5", "1.21.11", False),
        ("*", "1.21.11", True),
        ("[1.21,1.22)", "1.21.11", None),  # 不认识的 maven 范围 -> 跳过
    ],
)
def test_version_matches(spec: str, mc: str, expected: bool | None) -> None:
    assert m.version_matches(spec, mc) is expected


def test_version_matches_array_or(game: Path) -> None:
    # 数组表示"或"，这里以展示字符串形式 '||' 传入
    assert m.version_matches("1.20.1 || 1.21.11", "1.21.11") is True
    assert m.version_matches("1.19 || 1.20", "1.21.11") is False


def test_parse_server_jar() -> None:
    mc, loader = m.parse_server_jar("fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar")
    assert mc == "1.21.11"
    assert loader == "0.19.2"


# ---- 文件操作：启用 / 停用 ---------------------------------------------------


@pytest.fixture
async def engine(tmp_path: Path) -> Any:
    e = await open_db(tmp_path / "t.db")
    yield e
    await e.dispose()


async def test_disable_enable_moves_files(engine: Any, game: Path) -> None:
    make_jar(game / "mods" / "x.jar", {"id": "x", "version": "1"})
    r = await m.disable_mod(engine, game, "x.jar")
    assert r["enabled"] is False
    assert not (game / "mods" / "x.jar").exists()
    assert (game / "mods" / "disabled" / "x.jar").exists()

    r = await m.enable_mod(engine, game, "x.jar")
    assert r["enabled"] is True
    assert (game / "mods" / "x.jar").exists()
    assert not (game / "mods" / "disabled" / "x.jar").exists()

    # 待重启标记已写入
    pending = await m._kv_get(engine, m.KV_PENDING)
    assert isinstance(pending, dict) and len(pending["changes"]) == 2


# ---- 更新流程 ----------------------------------------------------------------


def _updates_doc(file: str, new_bytes: bytes, filename: str) -> dict[str, Any]:
    return {
        "updates": {
            file: {
                "source": "modrinth",
                "has_update": True,
                "sha512": hashlib.sha512(new_bytes).hexdigest(),
                "size": len(new_bytes),
                "url": "https://cdn.test/new.jar",
                "filename": filename,
                "project_id": "p",
                "version_number": "2.0",
            }
        }
    }


async def test_update_mod_success(engine: Any, game: Path) -> None:
    make_jar(game / "mods" / "old.jar", {"id": "u", "version": "1"})
    new_content = b"BRAND NEW JAR CONTENT"

    async def fake_download(url: str, dest: Path, max_bytes: int) -> int:
        dest.write_bytes(new_content)
        return len(new_content)

    updates = _updates_doc("old.jar", new_content, "u-2.0.jar")
    r = await m.update_mod(engine, game, "old.jar", updates, fake_download)
    assert r["new_file"] == "u-2.0.jar"
    assert (game / "mods" / "u-2.0.jar").read_bytes() == new_content
    assert not (game / "mods" / "old.jar").exists()
    # 旧 jar 以 .replaced- 存入 disabled
    replaced = list((game / "mods" / "disabled").glob("old.jar.replaced-*"))
    assert len(replaced) == 1


async def test_update_mod_sha_mismatch_keeps_old(engine: Any, game: Path) -> None:
    make_jar(game / "mods" / "old.jar", {"id": "u", "version": "1"})
    original = (game / "mods" / "old.jar").read_bytes()

    expected = b"the expected content"
    corrupted = b"X" * len(expected)  # 同样长度但内容不同 -> 过 size 检查，sha512 不符

    async def bad_download(url: str, dest: Path, max_bytes: int) -> int:
        dest.write_bytes(corrupted)
        return len(corrupted)

    # updates 里登记的 sha512 对应 expected，下载的是 corrupted，必然不符
    updates = _updates_doc("old.jar", expected, "u-2.0.jar")
    with pytest.raises(m.ModError, match="sha512"):
        await m.update_mod(engine, game, "old.jar", updates, bad_download)
    # 旧文件原封不动，.part 已清理
    assert (game / "mods" / "old.jar").read_bytes() == original
    assert not list((game / "mods").glob("*.part"))


async def test_update_mod_size_mismatch_keeps_old(engine: Any, game: Path) -> None:
    make_jar(game / "mods" / "old.jar", {"id": "u", "version": "1"})

    async def short_download(url: str, dest: Path, max_bytes: int) -> int:
        dest.write_bytes(b"short")
        return 5

    updates = _updates_doc("old.jar", b"a much longer expected content", "u-2.0.jar")
    with pytest.raises(m.ModError, match="大小不符"):
        await m.update_mod(engine, game, "old.jar", updates, short_download)
    assert (game / "mods" / "old.jar").exists()


# ---- 回退 --------------------------------------------------------------------


async def test_rollback_restores_old(engine: Any, game: Path) -> None:
    make_jar(game / "mods" / "old.jar", {"id": "u", "version": "1"})
    new_content = b"NEW JAR"

    async def fake_download(url: str, dest: Path, max_bytes: int) -> int:
        dest.write_bytes(new_content)
        return len(new_content)

    updates = _updates_doc("old.jar", new_content, "u-2.0.jar")
    await m.update_mod(engine, game, "old.jar", updates, fake_download)
    assert (game / "mods" / "u-2.0.jar").exists()

    r = await m.rollback_mod(engine, game, "u-2.0.jar")
    assert r["restored"] == "old.jar"
    assert (game / "mods" / "old.jar").exists()
    assert not (game / "mods" / "u-2.0.jar").exists()
    assert (game / "mods" / "disabled" / "u-2.0.jar").exists()


# ---- 路径穿越 ----------------------------------------------------------------


async def test_path_traversal_rejected(engine: Any, game: Path) -> None:
    make_jar(game / "mods" / "x.jar", {"id": "x", "version": "1"})
    for evil in ("../../etc/passwd", "..", "a/b.jar", "unknown.jar"):
        with pytest.raises(m.ModError):
            await m.disable_mod(engine, game, evil)


# ---- 更新检查（注入 HTTP） ---------------------------------------------------


async def test_check_updates_injected_http(engine: Any, game: Path) -> None:
    make_jar(game / "mods" / "a.jar", {"id": "a", "version": "1"})
    hashes = m._HashCache()
    mods = m.scan_mods(game, hashes)
    a_sha = next(x for x in mods if x.file == "a.jar").sha512

    async def fake_http(method: str, url: str, body: Any) -> Any:
        if "modrinth" in url:
            return {
                a_sha: {
                    "project_id": "p",
                    "id": "ver123",
                    "version_number": "2.0",
                    "date_published": "2026-01-01T00:00:00Z",
                    "changelog": "y" * 3000,
                    "files": [
                        {
                            "primary": True,
                            "filename": "a-2.0.jar",
                            "url": "https://cdn.test/a.jar",
                            "size": 999,
                            "hashes": {"sha512": "f" * 128},
                        }
                    ],
                }
            }
        return [{"loader": {"version": "0.20.0", "stable": True}}]

    result = await m.check_updates(mods, "1.21.11", fake_http)
    upd = result["updates"]["a.jar"]
    assert upd["source"] == "modrinth"
    assert upd["has_update"] is True
    assert upd["version_number"] == "2.0"
    assert len(upd["changelog"]) <= 2001
    assert result["loader_latest"] == "0.20.0"
    assert result["error"] is None


async def test_check_updates_network_error_friendly(engine: Any, game: Path) -> None:
    make_jar(game / "mods" / "a.jar", {"id": "a", "version": "1"})
    hashes = m._HashCache()
    mods = m.scan_mods(game, hashes)

    async def boom(method: str, url: str, body: Any) -> Any:
        raise RuntimeError("connection refused")

    result = await m.check_updates(mods, "1.21.11", boom)
    assert result["error"] is not None
    assert "Modrinth" in result["error"]  # 友好错误，不抛异常


# ---- pending_restart 自动失效 ------------------------------------------------


async def test_pending_restart_clears_after_restart(engine: Any) -> None:
    await m._record_pending(engine, {"action": "disable", "file": "x.jar"})
    v = await m._kv_get(engine, m.KV_PENDING)
    since = datetime.fromisoformat(v["since"])
    # 进程启动时间早于 since -> 仍待重启
    assert await m.pending_restart(engine, since.replace(year=since.year - 1)) is not None
    # 进程启动时间晚于 since -> 已生效，标记清除
    later = datetime.now(UTC).replace(year=datetime.now(UTC).year + 1)
    assert await m.pending_restart(engine, later) is None
    assert await m.pending_restart(engine, None) is None


# ---- API：observe 模式 409 + 列表接口 ---------------------------------------


@pytest.fixture
async def api_ctx(game: Path, tmp_path: Path) -> Any:
    from mcops.api import Services, create_app
    from mcops.auth import Authenticator, hash_password
    from mcops.backend import ObserveBackend
    from mcops.config import ApiConfig, ServerConfig
    from mcops.events import EventBus
    from mcops.players import ServerFiles
    from mcops.rcon import RconClient
    from mcops.server import ServerController
    from mcops.supervisor import Supervisor

    pw = "correct horse battery staple"
    e = await open_db(tmp_path / "api.db")
    bus = EventBus(e)
    cfg = ServerConfig(game_dir=game, jar="fabric-server-mc.*.jar")
    server = ServerController(
        cfg, ObserveBackend(cfg), RconClient("127.0.0.1", 1, ""), bus, UTC, observe=True
    )
    svc = Services(
        ApiConfig(),
        e,
        bus,
        server,
        Authenticator(e, hash_password(pw), None, 3600),
        Supervisor(),
        ServerFiles(game, None),
        None,
        "observe",
    )
    app = create_app()
    # 注册 mods 路由（mount_static 里才会调，这里单独调用）
    from mcops import api_mods

    api_mods.register(app)
    app.state.svc = svc
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as c:
        r = await c.post("/api/auth/login", json={"password": pw})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = c.cookies["mcops_csrf"]
        yield c
    await e.dispose()


async def test_api_overview_and_observe_409(api_ctx: Any, game: Path) -> None:
    make_jar(
        game / "mods" / "a.jar", {"id": "a", "version": "1", "depends": {"minecraft": "1.21.11"}}
    )
    make_jar(game / "mods" / "easyauth.jar", {"id": "easyauth", "version": "1"})
    c = api_ctx

    data = (await c.get("/api/mods")).json()
    assert data["mc_version"] == "1.21.11"
    assert data["loader_version"] == "0.19.2"
    assert {x["file"] for x in data["mods"]} == {"a.jar", "easyauth.jar"}

    # observe 模式：所有写操作返回 409，detail 以"观测模式"开头
    for path in (
        "/api/mods/a.jar/disable",
        "/api/mods/a.jar/enable",
        "/api/mods/a.jar/update",
        "/api/mods/update-all",
        "/api/mods/a.jar/rollback",
    ):
        r = await c.post(path)
        assert r.status_code == 409, path
        assert r.json()["detail"].startswith("观测模式"), path
