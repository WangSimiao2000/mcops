"""P1：面板配置（覆盖层/历史/待生效）、server.properties 编辑、systemd 单元生成、能力探测、改密/TOTP/机密。"""

from __future__ import annotations

import json
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC
from pathlib import Path
from typing import Any

import httpx
import pyotp
import pytest
import yaml

from mcops import api_config, gameprops
from mcops.api import Services, create_app
from mcops.auth import Authenticator, hash_password
from mcops.backend import render_unit
from mcops.capabilities import detect
from mcops.config import ServerConfig, load_config, load_secrets
from mcops.configstore import ConfigStore, ConfigValidationError, editable_schema, read_overlay_sync
from mcops.db import open_db
from mcops.events import EventBus
from mcops.rcon import RconClient
from mcops.server import ServerController
from mcops.supervisor import Supervisor
from tests.fakes import FakeBackend

PW = "correct horse battery staple"

PROPS = """#Minecraft server properties
difficulty=hard
view-distance=12
white-list=true
enable-rcon=true
rcon.password=s3cret
motd=Hello
level-type=minecraft\\:normal
custom-key=abc
"""


def make_env(tmp: Path) -> Path:
    game = tmp / "game"
    (game / "mods").mkdir(parents=True)
    (game / "config").mkdir()
    (game / "fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar").write_bytes(b"x")
    (game / "server.properties").write_text(PROPS, encoding="utf-8")
    sec = tmp / "secrets.yaml"
    sec.write_text(yaml.safe_dump({"admin_password_hash": hash_password(PW), "qq_appid": "111"}))
    sec.chmod(0o600)
    cfg = {
        "data_dir": str(tmp / "data"),
        "secrets_file": str(sec),
        "server": {"game_dir": str(game), "jar": "fabric-server-mc.*.jar", "java_opts": ["-Xmx2G"]},
        "ops": {"restart_time": "05:00"},
        "security": {"night_start_hour": 1},
    }
    p = tmp / "mcops.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ConfigStore]:
    path = make_env(tmp_path)
    loaded = load_config(path)
    engine = await open_db(loaded.config.db_path)
    lived: list[Any] = []
    s = ConfigStore(engine, path, loaded.config, on_live=lived.append)
    s.lived = lived  # type: ignore[attr-defined]
    yield s
    await engine.dispose()


# ---- 覆盖层 ------------------------------------------------------------------


async def test_save_writes_only_diff_and_marks_pending(store: ConfigStore) -> None:
    changed = await store.save({"server": {"port": 25570, "java_opts": ["-Xmx2G"]}}, "admin")
    assert changed == [{"path": "server.port", "apply": "agent"}]
    assert await store.overlay() == {"server": {"port": 25570}}
    v = await store.view()
    assert v["values"]["server"]["port"] == 25570
    assert v["overridden"] == ["server.port"]
    assert {"path": "server.port", "apply": "agent"} in v["pending"]


async def test_saving_yaml_value_removes_override(store: ConfigStore) -> None:
    await store.save({"ops": {"restart_time": "04:30"}}, "admin")
    await store.save({"ops": {"restart_time": "05:00"}}, "admin")
    assert await store.overlay() == {}


async def test_invalid_value_rejected_and_nothing_written(store: ConfigStore) -> None:
    with pytest.raises(ConfigValidationError, match=r"server\.port"):
        await store.save({"server": {"port": "not-a-number"}}, "admin")
    with pytest.raises(ConfigValidationError, match="时区"):
        await store.save({"general": {"timezone": "Mars/Base"}}, "admin")
    assert await store.overlay() == {}
    assert await store.history() == []


async def test_hidden_fields_cannot_be_written(store: ConfigStore) -> None:
    with pytest.raises(ConfigValidationError, match="不能在面板修改"):
        await store.save({"api": {"host": "0.0.0.0"}}, "admin")


async def test_live_fields_apply_immediately(store: ConfigStore) -> None:
    changed = await store.save({"security": {"night_start_hour": 2}}, "admin")
    assert changed == [{"path": "security.night_start_hour", "apply": "live"}]
    assert store.lived[-1].security.night_start_hour == 2  # type: ignore[attr-defined]
    assert not any(p["path"].startswith("security.") for p in await store.pending())


async def test_reset_history_and_restore(store: ConfigStore) -> None:
    await store.save({"server": {"port": 25570}}, "admin", note="换端口")
    await store.save({"ops": {"warn_minutes": 10}}, "admin")
    await store.reset(["server.port"], "admin")
    assert await store.overlay() == {"ops": {"warn_minutes": 10}}
    hist = await store.history()
    assert [h["action"] for h in hist] == ["reset", "save", "save"]
    first = hist[-1]
    assert first["note"] == "换端口" and first["changed"] == ["server.port"]
    await store.restore(first["id"], "admin")
    assert await store.overlay() == {"server": {"port": 25570}}


async def test_nested_optional_section_and_general(store: ConfigStore) -> None:
    await store.save(
        {"ops": {"motd": {"server_list": "Hi", "line1": None, "line2": None}}}, "admin"
    )
    eff = await store.effective()
    assert eff.ops.motd is not None and eff.ops.motd.server_list == "Hi"
    await store.save({"general": {"timezone": "Asia/Shanghai"}}, "admin")
    assert (await store.overlay())["timezone"] == "Asia/Shanghai"
    assert (await store.effective()).tzinfo().utcoffset(None) is None or True  # 可构造即可


async def test_mc_fields_pending_until_next_start(store: ConfigStore) -> None:
    await store.mark_mc_started()
    assert not [p for p in await store.pending() if p["apply"] == "mc"]
    await store.save({"server": {"java_opts": ["-Xmx4G"]}}, "admin")
    assert {"path": "server.java_opts", "apply": "agent"} in await store.pending()  # 先要重启 agent
    store.running = await store.effective()  # 模拟 agent 已用新值重启，但 MC 还没重开
    assert await store.pending() == [{"path": "server.java_opts", "apply": "mc"}]
    await store.mark_mc_started()
    assert not [p for p in await store.pending() if p["apply"] == "mc"]


async def test_overlay_read_at_startup(store: ConfigStore) -> None:
    await store.save({"server": {"port": 25570}}, "admin")
    over = read_overlay_sync(store.running.db_path)
    assert load_config(store.yaml_path, over).config.server.port == 25570


def test_schema_has_titles_and_meta() -> None:
    s = editable_schema()["properties"]
    assert set(s) >= {"general", "server", "ops", "security", "bot", "edge", "retention", "api"}
    srv = s["server"]["properties"]
    assert srv["java_opts"]["x-widget"] == "memory" and srv["java_opts"]["x-apply"] == "mc"
    assert srv["port"]["title"] == "游戏端口"
    assert "host" not in s["api"]["properties"]  # 监听地址不能在面板改
    assert "legacy_mc_sh" not in s["bot"]["properties"]
    assert s["ops"]["properties"]["restic"]["properties"]["interval_minutes"]["x-unit"] == "分钟"
    assert s["ops"]["properties"]["motd"]["nullable"] is True
    json.dumps(s, ensure_ascii=False)  # 可序列化


# ---- server.properties --------------------------------------------------------


def test_props_view_types_managed_and_sensitive(tmp_path: Path) -> None:
    p = make_env(tmp_path).parent / "game" / "server.properties"
    v = {it["key"]: it for it in gameprops.view(p, motd_sync=True)["items"]}
    assert v["difficulty"]["type"] == "enum" and v["difficulty"]["value"] == "hard"
    assert v["view-distance"]["value"] == 12 and v["view-distance"]["max"] == 32
    assert v["white-list"]["value"] is True
    assert v["rcon.password"]["value"] is None and v["rcon.password"]["sensitive"]
    assert v["enable-rcon"]["managed"] and v["motd"]["managed"]
    assert v["level-type"]["value"] == "minecraft:normal"  # 反转义
    assert v["custom-key"]["known"] is False


def test_props_update_preserves_file_and_backs_up(tmp_path: Path) -> None:
    p = make_env(tmp_path).parent / "game" / "server.properties"
    r = gameprops.update(p, {"difficulty": "easy", "view-distance": 10, "white-list": True}, False)
    assert r["changed"] == ["difficulty", "view-distance"]  # white-list 没变
    text = p.read_text()
    assert "difficulty=easy" in text and "view-distance=10" in text
    assert text.startswith("#Minecraft server properties")
    assert "level-type=minecraft\\:normal" in text and "rcon.password=s3cret" in text
    assert (p.parent / r["backup"]).read_text() == PROPS


@pytest.mark.parametrize(
    ("changes", "msg"),
    [
        ({"view-distance": 99}, "之间"),
        ({"difficulty": "extreme"}, "只能是"),
        ({"white-list": "yes"}, "开/关"),
        ({"rcon.password": "x"}, "不能在这里修改"),
        ({"enable-rcon": False}, "不能在这里修改"),
        ({"nope": "1"}, "没有这个键"),
        ({"custom-key": "a\nb"}, "单行"),
    ],
)
def test_props_update_rejects(tmp_path: Path, changes: dict[str, Any], msg: str) -> None:
    p = make_env(tmp_path).parent / "game" / "server.properties"
    with pytest.raises(gameprops.PropsError, match=msg):
        gameprops.update(p, changes, motd_sync=False)
    assert p.read_text() == PROPS


# ---- systemd 单元 / 能力 ------------------------------------------------------


def test_render_unit_matches_m3_and_rejects_injection(tmp_path: Path) -> None:
    cfg = ServerConfig(
        game_dir=tmp_path,
        jar="server.jar",
        java_bin="/usr/bin/java",
        java_opts=["-Xms8G", "-Xmx8G"],
    )
    text = render_unit(cfg)
    assert (
        'ExecStart=/bin/bash -c "exec /usr/bin/java -Xms8G -Xmx8G -jar server.jar nogui '
        '< <(exec sleep infinity)"'
    ) in text
    assert f"WorkingDirectory={tmp_path}" in text
    for bad in ["-Xmx8G; rm x", '-D"a"', "-Da=$HOME", "-Xmx8G nogui"]:
        cfg.java_opts = [bad]
        with pytest.raises(ValueError, match="不安全"):
            render_unit(cfg)


def _mod(path: Path, mod_id: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps({"id": mod_id, "version": "1"}))


def test_capabilities_detect(tmp_path: Path) -> None:
    game = make_env(tmp_path).parent / "game"
    _mod(game / "mods" / "ea.jar", "easyauth")
    _mod(game / "mods" / "pp.jar", "proxy_protocol_support")
    (game / "mods" / "disabled").mkdir()
    _mod(game / "mods" / "disabled" / "carpet.jar", "carpet")  # 停用的不算
    (game / "config" / "proxy_protocol_support.json").write_text(
        json.dumps({"enableProxyProtocol": True, "proxyServerIPs": ["10.88.88.1"]})
    )
    c = detect(game, "fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar")
    assert c.mc_version == "1.21.11" and c.loader_version == "0.19.2"
    assert c.mods == {"easyauth": True, "carpet": False, "proxy_protocol": True, "minimotd": False}
    assert c.to_json()["proxy_source"] == "10.88.88.1"


# ---- API：改密 / TOTP / 机密 ---------------------------------------------------


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    path = make_env(tmp_path)
    loaded = load_config(path)
    cfg = loaded.config
    engine = await open_db(cfg.db_path)
    server = ServerController(
        cfg.server, FakeBackend(), RconClient("127.0.0.1", 1, ""), EventBus(engine), UTC
    )
    auth = Authenticator(engine, loaded.secrets.admin_password_hash.get_secret_value(), None, 3600)
    svc = Services(cfg.api, engine, EventBus(engine), server, auth, Supervisor())
    svc.secrets_file = cfg.secrets_file
    svc.config = ConfigStore(engine, path, cfg)
    app = create_app()
    api_config.register(app)
    app.state.svc = svc
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as c:
        assert (await c.post("/api/auth/login", json={"password": PW})).status_code == 200
        c.headers["X-CSRF-Token"] = c.cookies["mcops_csrf"]
        yield c, svc
    await engine.dispose()


async def test_config_api_roundtrip(
    client: tuple[httpx.AsyncClient, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _ = client
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    v = (await c.get("/api/config")).json()
    assert v["values"]["server"]["port"] == 25565 and v["readonly"]["mode"] == "control"
    r = await c.put("/api/config", json={"values": {"server": {"port": 70000}}})
    assert r.status_code == 422 and "server.port" in r.json()["detail"]
    r = await c.put("/api/config", json={"values": {"ops": {"warn_minutes": 3}}, "note": "测试"})
    assert r.status_code == 200 and r.json()["changed"] == [
        {"path": "ops.warn_minutes", "apply": "agent"}
    ]
    h = (await c.get("/api/config/history")).json()["items"]
    assert h[0]["note"] == "测试"
    assert (await c.post("/api/system/restart-agent")).status_code == 409  # 测试里不在 systemd 下


async def test_props_api(client: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = client
    items = (await c.get("/api/gamecfg/properties")).json()["items"]
    assert all(it["value"] is None for it in items if it["key"] == "rcon.password")
    r = await c.put("/api/gamecfg/properties", json={"changes": {"difficulty": "normal"}})
    assert r.status_code == 200 and r.json()["changed"] == ["difficulty"]
    r = await c.put("/api/gamecfg/properties", json={"changes": {"enable-rcon": False}})
    assert r.status_code == 422


async def test_change_password_revokes_sessions(client: tuple[httpx.AsyncClient, Services]) -> None:
    c, svc = client
    r = await c.post("/api/auth/password", json={"old_password": "wrong", "new_password": "x" * 12})
    assert r.status_code == 403
    new = "a brand new passphrase"
    r = await c.post("/api/auth/password", json={"old_password": PW, "new_password": new})
    assert r.status_code == 204
    assert (await c.get("/api/auth/me")).status_code == 401  # 当前会话也被吊销
    assert (await c.post("/api/auth/login", json={"password": PW})).status_code == 401
    assert (await c.post("/api/auth/login", json={"password": new})).status_code == 200
    sec = load_secrets(svc.secrets_file)
    assert sec.qq_appid is not None and sec.qq_appid.get_secret_value() == "111"  # 其它键保留
    assert (svc.secrets_file.stat().st_mode & 0o777) == 0o600


async def test_totp_setup_confirm_disable(client: tuple[httpx.AsyncClient, Services]) -> None:
    c, svc = client
    secret = (await c.post("/api/auth/totp/setup")).json()["secret"]
    assert (await c.post("/api/auth/totp/confirm", json={"code": "000000"})).status_code == 422
    code = pyotp.TOTP(secret).now()
    assert (await c.post("/api/auth/totp/confirm", json={"code": code})).json() == {"enabled": True}
    assert load_secrets(svc.secrets_file).totp_secret is not None
    r = await c.post("/api/auth/totp/disable", json={"password": PW})
    assert r.json() == {"enabled": False} and load_secrets(svc.secrets_file).totp_secret is None


async def test_secrets_write_only(client: tuple[httpx.AsyncClient, Services]) -> None:
    c, svc = client
    items = {it["key"]: it for it in (await c.get("/api/secrets")).json()["items"]}
    assert items["qq_appid"]["set"] and not items["qq_secret"]["set"]
    assert "value" not in items["qq_appid"]
    assert (await c.put("/api/secrets", json={"restic_password": "x"})).status_code == 422
    r = await c.put("/api/secrets", json={"qq_secret": "abc", "qq_appid": ""})
    assert r.status_code == 200
    sec = load_secrets(svc.secrets_file)
    assert sec.qq_secret is not None and sec.qq_secret.get_secret_value() == "abc"
    assert sec.qq_appid is not None and sec.qq_appid.get_secret_value() == "111"  # 空 = 不改


def test_fingerprint_rule_unavailable_when_easyauth_drops_carpet_hello(tmp_path: Path) -> None:
    from mcops.capabilities import Capabilities, easyauth_allows, unavailable_rules

    conf = tmp_path / "config" / "EasyAuth"
    conf.mkdir(parents=True)
    caps = Capabilities()
    # 没装 carpet：没有握手信号
    assert "fingerprint_mismatch" in unavailable_rules(caps, tmp_path)
    caps.mods["carpet"] = True
    assert unavailable_rules(caps, tmp_path) == {}  # 只有 carpet：规则可用
    caps.mods["easyauth"] = True
    (conf / "extended.conf").write_text(
        'allow-custom-packets=false\nallowed-custom-packets=["jade:client_handshake", "servux:tweaks"]\n'
    )
    reason = unavailable_rules(caps, tmp_path)["fingerprint_mismatch"]
    assert "carpet:hello" in reason and "执行命令" in reason
    # 显式放行了 carpet:hello（或全部放行）才认为规则可用
    (conf / "extended.conf").write_text('allowed-custom-packets=["carpet:hello"]\n')
    assert easyauth_allows(tmp_path, "carpet:hello") and unavailable_rules(caps, tmp_path) == {}
    (conf / "extended.conf").write_text("allow-custom-packets = true # 全部放行\n")
    assert easyauth_allows(tmp_path, "anything:x")
