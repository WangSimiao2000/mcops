from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from mcops.api import Services, create_app
from mcops.auth import Authenticator, hash_password
from mcops.backend import ObserveBackend
from mcops.config import ApiConfig, ServerConfig
from mcops.db import open_db
from mcops.events import Event, EventBus
from mcops.pipeline import Pipeline
from mcops.players import PlayerTracker, ServerFiles
from mcops.rcon import RconClient
from mcops.security import RULES, SecurityEngine
from mcops.server import ServerController
from mcops.supervisor import Supervisor

PW = "correct horse battery staple"
CST = timezone(timedelta(hours=8))


@pytest.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Services, Pipeline]]:
    game = tmp_path / "game"
    game.mkdir()
    (game / "whitelist.json").write_text(json.dumps([{"uuid": "u", "name": "Alice"}]))
    (game / "banned-ips.json").write_text(json.dumps([{"ip": "198.51.100.66"}]))
    engine = await open_db(tmp_path / "t.db")
    bus = EventBus(engine)
    cfg = ServerConfig(game_dir=game, jar="server.jar")
    server = ServerController(
        cfg, ObserveBackend(cfg), RconClient("127.0.0.1", 1, ""), bus, CST, observe=True
    )
    svc = Services(
        ApiConfig(),
        engine,
        bus,
        server,
        Authenticator(engine, hash_password(PW), None, 3600),
        Supervisor(),
        ServerFiles(game, None),
        None,
        "observe",
    )
    app = create_app()
    app.state.svc = svc
    pipe = Pipeline(PlayerTracker(engine), SecurityEngine(engine, CST, on_alert=svc.alerts.publish))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as c:
        r = await c.post("/api/auth/login", json={"password": PW})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = c.cookies["mcops_csrf"]
        yield c, svc, pipe
    await engine.dispose()


async def test_observe_mode_blocks_control(
    ctx: tuple[httpx.AsyncClient, Services, Pipeline],
) -> None:
    c, _, _ = ctx
    assert (await c.get("/api/system")).json()["mode"] == "observe"
    for path in ("/api/server/start", "/api/server/stop", "/api/server/restart"):
        r = await c.post(path)
        assert r.status_code == 409, path
        assert "观测模式" in r.json()["detail"]
    r = await c.post("/api/server/command", json={"command": "op attacker"})
    assert r.status_code == 409


async def test_players_ips_alerts_endpoints(
    ctx: tuple[httpx.AsyncClient, Services, Pipeline],
) -> None:
    c, _, pipe = ctx
    t = datetime(2026, 10, 11, 3, 0, tzinfo=CST).astimezone(UTC)
    for name in ("Alice", "Mallory", "Eve"):
        await pipe.process(
            Event(
                "player_login",
                "mc_log",
                {"name": name, "ip": "198.51.100.66", "port": 1, "entity_id": 1, "fake": False},
                ts=t,
            )
        )
    players = (await c.get("/api/players")).json()["items"]
    alice = next(p for p in players if p["name"] == "Alice")
    assert alice["online"] and alice["whitelisted"] and alice["last_ip"] == "198.51.100.66"
    assert alice["easyauth_registered"] is None  # 未配置数据库：未知

    detail = (await c.get("/api/players/Alice")).json()
    assert detail["sessions"][0]["ip_trusted"] is True
    assert (await c.get("/api/players/no%3Bpe")).status_code == 400
    assert (await c.get("/api/players/Nobody")).status_code == 404

    ip = (await c.get("/api/ips/198.51.100.66")).json()
    assert ip["banned"] is True
    assert {a["name"] for a in ip["accounts"]} == {"Alice", "Mallory", "Eve"}
    assert (await c.get("/api/ips/not-an-ip")).status_code == 400

    alerts = (await c.get("/api/alerts")).json()
    rules = {a["rule"] for a in alerts["items"]}
    assert {"ip_multi_account", "night_login", "new_account"} <= rules
    assert alerts["open_counts"]["critical"] >= 1

    one = alerts["items"][0]["id"]
    assert (await c.post(f"/api/alerts/{one}/ack")).json() == {"ok": True}
    n = (await c.post("/api/alerts/ack-all", json={})).json()["acked"]
    assert n >= 1
    assert (await c.get("/api/alerts")).json()["open_counts"] == {
        "critical": 0,
        "warning": 0,
        "info": 0,
    }

    rules_list = (await c.get("/api/security/rules")).json()["items"]
    assert len(rules_list) == len(RULES) and all(r["enabled"] for r in rules_list)
    assert (await c.get("/api/edge/status")).json()["enabled"] is False


async def test_observe_backend_finds_process_by_cwd(tmp_path: Path) -> None:
    # 没有匹配的进程：不应误认为在运行
    cfg = ServerConfig(game_dir=tmp_path, jar="definitely-not-running-xyz.jar")
    assert await ObserveBackend(cfg).pid() is None
    with pytest.raises(RuntimeError):
        await ObserveBackend(cfg).start()


async def test_bot_and_whitelist_endpoints(
    ctx: tuple[httpx.AsyncClient, Services, Pipeline], tmp_path: Path
) -> None:
    from mcops.bot.core import BotCore, BotSettings
    from mcops.bot.runtime import BotRuntime
    from mcops.bot.whitelist import WhitelistFile, offline_uuid

    c, svc, _ = ctx
    assert (await c.get("/api/bot/status")).status_code == 503  # 未配置机器人
    assert svc.files is not None
    game = svc.files.game_dir
    wl = WhitelistFile(game / "whitelist.json")

    class Echo:
        async def run(self, command: str) -> str | None:
            names = wl.names()
            return (
                f"There are {len(names)} whitelisted player(s): {', '.join(names)}"
                if command == "whitelist list"
                else ""
            )

    async def no_pid() -> int | None:
        return None

    core = BotCore(
        svc.engine,
        BotSettings(display_host="mc.example.test", admin_openids={"ADM"}),
        wl,
        Echo(),
        svc.files.game_dir,
        no_pid,
    )
    svc.bot = BotRuntime(svc.engine, core, None, enabled=False)

    st = (await c.get("/api/bot/status")).json()
    assert st["enabled"] is False and st["connected"] is False

    r = (
        await c.post(
            "/api/bot/simulate", json={"content": "申请白名单 NewGuy_1", "as_admin": False}
        )
    ).json()
    assert r["cmd"] == "apply" and "已登记" in r["reply"]
    apps = (await c.get("/api/bot/applications")).json()["items"]
    assert apps[0]["name"] == "NewGuy_1"
    assert apps[0]["openid_masked"] == "WEB_…UEST"  # 申请人 openid 只以掩码外显
    r = (await c.post("/api/bot/applications/NewGuy_1/approve")).json()
    assert r["reply"].startswith("✅")
    items = (await c.get("/api/whitelist")).json()["items"]
    assert {i["name"] for i in items} == {"Alice", "NewGuy_1"}
    new = next(i for i in items if i["name"] == "NewGuy_1")
    assert new["uuid"] == offline_uuid("NewGuy_1")
    assert (await c.post("/api/whitelist", json={"name": "bad name"})).status_code == 400
    assert (await c.delete("/api/whitelist/NewGuy_1")).json()["reply"].startswith("🗑")
    inter = (await c.get("/api/bot/interactions?q=NewGuy")).json()["items"]
    assert inter and inter[0]["cmd"] == "apply"
