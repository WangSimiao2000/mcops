"""网络页（M4）后端测试：连接统计、IP 标签、过滤分页、封禁管理。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.api import Services, create_app
from mcops.auth import Authenticator, hash_password
from mcops.config import ApiConfig, ServerConfig
from mcops.db import open_db
from mcops.events import EventBus
from mcops.players import ServerFiles
from mcops.rcon import RconClient
from mcops.server import ServerController, State
from mcops.supervisor import Supervisor
from tests.fakes import FakeBackend, FakeRconServer

PW = "correct horse battery staple"
CST = timezone(timedelta(hours=8))
NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(minutes=30)
# 相对真实时间播种：窗口是按真实 now 算的，固定日期会随日历流逝而落出窗口（曾导致本测试偶发失败）。


# ---- 数据播种 --------------------------------------------------------------


async def _seed(engine: AsyncEngine) -> None:
    """造 frps_conns 与 player_sessions：
    - 1.2.3.4：玩家 Alice，minecraft 连接若干 → player
    - 93.184.216.198：只连 minecraft 15 次、从没进服 → scanner
    - 203.0.113.9：连过 ssh → ssh（敏感）
    - 198.51.100.66：已封禁（banned-ips.json）+ minecraft 连接 → banned
    """
    conns: list[dict[str, object]] = []

    def add(ip: str, proxy: str, n: int, start_min_ago: int) -> None:
        for i in range(n):
            ts = NOW - timedelta(minutes=start_min_ago - i)
            conns.append({"ts": ts, "proxy": proxy, "ip": ip, "port": 50000 + i})

    add("1.2.3.4", "minecraft", 4, 60)
    add("93.184.216.198", "minecraft", 15, 600)  # 跨 10 小时内，24h 窗口可见
    add("203.0.113.9", "ssh", 3, 30)
    add("198.51.100.66", "minecraft", 2, 90)
    # 7d 窗口才可见的旧连接（1h/24h 看不到）
    conns.append(
        {"ts": NOW - timedelta(days=3), "proxy": "minecraft", "ip": "8.8.8.8", "port": 40000}
    )

    sessions = [
        {
            "name": "Alice",
            "login_at": NOW - timedelta(minutes=55),
            "logout_at": None,
            "ip": "1.2.3.4",
            "ip_trusted": True,
            "port": 50000,
            "fake": False,
            "source": "live",
        }
    ]
    async with engine.begin() as conn:
        await conn.execute(insert(db.frps_conns), conns)
        await conn.execute(insert(db.player_sessions), sessions)


@pytest.fixture
async def observe_ctx(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    """观测模式：验证读接口与写接口 409。"""
    game = tmp_path / "game"
    game.mkdir()
    (game / "banned-ips.json").write_text(
        json.dumps(
            [{"ip": "198.51.100.66", "reason": "测试", "created": "2026-10-01", "source": "cli"}]
        )
    )
    engine = await open_db(tmp_path / "t.db")
    await _seed(engine)
    bus = EventBus(engine)
    cfg = ServerConfig(game_dir=game, jar="server.jar")
    from mcops.backend import ObserveBackend

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
    # 注册 network 路由（mount_static 里才会调，测试里单独调用）
    from mcops import api_network

    api_network.register(app)
    app.state.svc = svc
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as c:
        r = await c.post("/api/auth/login", json={"password": PW})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = c.cookies["mcops_csrf"]
        yield c, svc
    await engine.dispose()


@pytest.fixture
async def control_ctx(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, Services, FakeRconServer]]:
    """control 模式 + 运行中的服务端 + 假 RCON：验证 ban/pardon 的 RCON 命令。"""
    game = tmp_path / "game"
    game.mkdir()
    (game / "banned-ips.json").write_text(json.dumps([{"ip": "198.51.100.66"}]))
    engine = await open_db(tmp_path / "t.db")
    await _seed(engine)
    bus = EventBus(engine)

    def handler(cmd: str) -> str:
        return f"OK: {cmd}"

    fake = FakeRconServer(PW, handler)
    async with fake.running():
        cfg = ServerConfig(game_dir=game, jar="server.jar")
        rcon = RconClient("127.0.0.1", fake.port, PW)
        backend = FakeBackend()
        backend.alive = True
        server = ServerController(cfg, backend, rcon, bus, CST, observe=False)
        server.state = State.RUNNING
        svc = Services(
            ApiConfig(),
            engine,
            bus,
            server,
            Authenticator(engine, hash_password(PW), None, 3600),
            Supervisor(),
            ServerFiles(game, None),
            None,
            "control",
        )
        app = create_app()
        from mcops import api_network

        api_network.register(app)
        app.state.svc = svc
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as c:
            r = await c.post("/api/auth/login", json={"password": PW})
            assert r.status_code == 200
            c.headers["X-CSRF-Token"] = c.cookies["mcops_csrf"]
            yield c, svc, fake
    await engine.dispose()


# ---- 统计聚合 --------------------------------------------------------------


async def test_stats_counts_and_series(
    observe_ctx: tuple[httpx.AsyncClient, Services],
) -> None:
    c, _ = observe_ctx
    # 把"现在"钉在 NOW 之后一点：直接用 24h 窗口（NOW 的数据都在 24h 内，8.8.8.8 的在 3 天前）。
    # 由于测试里 datetime.now 真实流动，用 30d 窗口确保全部命中后再比 24h 的子集。
    data = (await c.get("/api/network/stats?window=30d")).json()
    by = data["by_proxy"]
    assert by["minecraft"]["connections"] == 4 + 15 + 2 + 1  # 含 8.8.8.8
    assert by["ssh"]["connections"] == 3
    assert by["minecraft"]["distinct_ips"] == 4  # 1.2.3.4,176..,198..,8.8.8.8
    assert by["ssh"]["distinct_ips"] == 1
    assert data["bucket"] == "day"
    assert isinstance(data["series"], list) and data["series"]

    hourly = (await c.get("/api/network/stats?window=24h")).json()
    assert hourly["bucket"] == "hour"
    # 24h 窗口看不到 3 天前的 8.8.8.8
    assert hourly["by_proxy"]["minecraft"]["distinct_ips"] == 3


async def test_stats_buckets_in_local_time(tmp_path: Path) -> None:
    """ts 存 UTC，分桶按北京时间（+480 分钟）；tz_offset_min=0 时退回 UTC。"""
    from mcops import network

    engine = await open_db(tmp_path / "tz.db")
    ts = (datetime.now(UTC) - timedelta(hours=2)).replace(second=0, microsecond=0)
    async with engine.begin() as conn:
        await conn.execute(
            insert(db.frps_conns), [{"ts": ts, "proxy": "minecraft", "ip": "1.2.3.4", "port": 1}]
        )
    local = ts.astimezone(CST)
    hourly = await network.stats(engine, "24h", tz_offset_min=480)
    assert hourly["tz_offset_min"] == 480
    assert [p["bucket"] for p in hourly["series"]] == [local.strftime("%Y-%m-%dT%H:00")]
    daily = await network.stats(engine, "7d", tz_offset_min=480)
    assert [p["bucket"] for p in daily["series"]] == [local.strftime("%Y-%m-%d")]
    utc = await network.stats(engine, "24h", tz_offset_min=0)
    assert [p["bucket"] for p in utc["series"]] == [ts.strftime("%Y-%m-%dT%H:00")]
    await engine.dispose()


async def test_stats_rejects_bad_window(
    observe_ctx: tuple[httpx.AsyncClient, Services],
) -> None:
    c, _ = observe_ctx
    r = await c.get("/api/network/stats?window=90d")
    assert r.status_code == 422


# ---- IP 标签 / 过滤 / 分页 -------------------------------------------------


async def test_ip_tags(observe_ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = observe_ctx
    items = (await c.get("/api/network/ips?window=30d&limit=100")).json()["items"]
    by_ip = {it["ip"]: it for it in items}
    assert "player" in by_ip["1.2.3.4"]["tags"]
    assert by_ip["1.2.3.4"]["players"] == ["Alice"]
    assert "scanner" in by_ip["93.184.216.198"]["tags"]
    assert "player" not in by_ip["93.184.216.198"]["tags"]
    assert "ssh" in by_ip["203.0.113.9"]["tags"]
    assert "banned" in by_ip["198.51.100.66"]["tags"]


async def test_ip_filter_by_tag_and_query(
    observe_ctx: tuple[httpx.AsyncClient, Services],
) -> None:
    c, _ = observe_ctx
    scanners = (await c.get("/api/network/ips?window=30d&tag=scanner")).json()["items"]
    assert [it["ip"] for it in scanners] == ["93.184.216.198"]

    q = (await c.get("/api/network/ips?window=30d&q=1.2.3")).json()["items"]
    assert [it["ip"] for it in q] == ["1.2.3.4"]

    # 按玩家名过滤
    byname = (await c.get("/api/network/ips?window=30d&q=alice")).json()["items"]
    assert [it["ip"] for it in byname] == ["1.2.3.4"]


async def test_ip_pagination(observe_ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = observe_ctx
    page1 = (await c.get("/api/network/ips?window=30d&limit=2&offset=0")).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    page2 = (await c.get("/api/network/ips?window=30d&limit=2&offset=2")).json()
    assert len(page2["items"]) == 2
    # 不重叠
    assert not ({it["ip"] for it in page1["items"]} & {it["ip"] for it in page2["items"]})


async def test_recent_flow(observe_ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = observe_ctx
    data = (await c.get("/api/network/recent?limit=200")).json()
    assert data["items"]
    first = data["items"][0]
    assert {"ts", "proxy", "ip", "port", "tags", "players"} <= set(first)


async def test_ssh_sources(observe_ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = observe_ctx
    data = (await c.get("/api/network/ssh")).json()
    assert any(it["ip"] == "203.0.113.9" for it in data["items"])


# ---- 封禁管理：观测模式 409 ------------------------------------------------


async def test_bans_list_readable(observe_ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = observe_ctx
    data = (await c.get("/api/network/bans")).json()
    assert any(b["ip"] == "198.51.100.66" and b["reason"] == "测试" for b in data["items"])


async def test_observe_mode_blocks_writes(
    observe_ctx: tuple[httpx.AsyncClient, Services],
) -> None:
    c, _ = observe_ctx
    r = await c.post("/api/network/bans", json={"ip": "5.6.7.8"})
    assert r.status_code == 409
    assert "观测模式" in r.json()["detail"]
    r = await c.post("/api/network/bans/5.6.7.8/pardon")
    assert r.status_code == 409
    r = await c.post("/api/network/scanners/ban", json={"window": "24h"})
    assert r.status_code == 409


# ---- 封禁管理：control 模式 ------------------------------------------------


async def test_ban_executes_rcon_and_audits(
    control_ctx: tuple[httpx.AsyncClient, Services, FakeRconServer],
) -> None:
    c, svc, fake = control_ctx
    r = await c.post("/api/network/bans", json={"ip": "4.4.4.4", "reason": "恶意扫描"})
    assert r.status_code == 200, r.text
    assert r.json()["ip"] == "4.4.4.4"
    assert "ban-ip 4.4.4.4 恶意扫描" in fake.commands

    # 审计 + 事件
    async with svc.engine.connect() as conn:
        audits = (
            (await conn.execute(select(db.audit_log).where(db.audit_log.c.action == "network.ban")))
            .mappings()
            .all()
        )
        assert audits and audits[-1]["target"] == "4.4.4.4"
        evs = (
            (await conn.execute(select(db.events).where(db.events.c.type == "ip_banned")))
            .mappings()
            .all()
        )
        assert evs and evs[-1]["data"]["ip"] == "4.4.4.4"


async def test_pardon_executes_rcon(
    control_ctx: tuple[httpx.AsyncClient, Services, FakeRconServer],
) -> None:
    c, svc, fake = control_ctx
    r = await c.post("/api/network/bans/198.51.100.66/pardon")
    assert r.status_code == 200
    assert "pardon-ip 198.51.100.66" in fake.commands
    async with svc.engine.connect() as conn:
        evs = (
            (await conn.execute(select(db.events).where(db.events.c.type == "ip_unbanned")))
            .mappings()
            .all()
        )
        assert evs and evs[-1]["data"]["ip"] == "198.51.100.66"


async def test_reject_illegal_ip(
    control_ctx: tuple[httpx.AsyncClient, Services, FakeRconServer],
) -> None:
    c, _, fake = control_ctx
    r = await c.post("/api/network/bans", json={"ip": "not-an-ip"})
    assert r.status_code == 422
    r = await c.post("/api/network/bans/also-bad/pardon")
    assert r.status_code == 422
    assert not any("not-an-ip" in cmd for cmd in fake.commands)


async def test_reject_internal_ips(
    control_ctx: tuple[httpx.AsyncClient, Services, FakeRconServer],
) -> None:
    c, _, fake = control_ctx
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1", "10.88.88.1"):
        r = await c.post("/api/network/bans", json={"ip": ip})
        assert r.status_code == 422, ip
    assert not fake.commands  # 没有任何 RCON 命令被执行


async def test_scanner_ban_preview_and_execute(
    control_ctx: tuple[httpx.AsyncClient, Services, FakeRconServer],
) -> None:
    c, _, fake = control_ctx
    preview = (await c.get("/api/network/scanners?window=30d")).json()
    assert any(it["ip"] == "93.184.216.198" for it in preview["items"])

    r = await c.post("/api/network/scanners/ban", json={"window": "30d"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] >= 1
    assert any("ban-ip 93.184.216.198" in cmd for cmd in fake.commands)


async def test_scanner_ban_50_cap(
    control_ctx: tuple[httpx.AsyncClient, Services, FakeRconServer],
) -> None:
    c, _, _ = control_ctx
    too_many = [f"5.0.0.{i}" for i in range(51)]
    r = await c.post("/api/network/scanners/ban", json={"window": "24h", "ips": too_many})
    assert r.status_code == 422
