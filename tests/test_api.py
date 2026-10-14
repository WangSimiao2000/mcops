from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from mcops.api import Services, create_app
from mcops.auth import Authenticator, hash_password
from mcops.config import ApiConfig, ServerConfig
from mcops.db import open_db
from mcops.events import Event, EventBus
from mcops.rcon import RconClient
from mcops.server import ServerController, State
from mcops.supervisor import Supervisor
from tests.fakes import FakeBackend, FakeRconServer

PASSWORD = "correct horse battery staple"
BASE = "http://127.0.0.1:8765"


class Env:
    def __init__(self, app: FastAPI, svc: Services, backend: FakeBackend, rcon: FakeRconServer):
        self.app, self.svc, self.backend, self.rcon = app, svc, backend, rcon


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    rcon_srv = FakeRconServer("rpw", lambda c: f"ran:{c}")
    async with rcon_srv.running():
        engine = await open_db(tmp_path / "t.db")
        bus = EventBus(engine)
        cfg = ServerConfig(
            game_dir=tmp_path / "game",
            jar="server.jar",
            rcon_port=rcon_srv.port,
            start_ready_timeout_s=3,
            stop_timeout_s=1,
        )
        backend = FakeBackend()
        server = ServerController(
            cfg,
            backend,
            RconClient("127.0.0.1", rcon_srv.port, "rpw"),
            bus,
            timezone(timedelta(hours=8)),
        )
        auth = Authenticator(engine, hash_password(PASSWORD), None, 3600)
        svc = Services(ApiConfig(), engine, bus, server, auth, Supervisor())

        async def factory() -> Services:
            return svc

        app = create_app(factory)
        app.state.svc = svc
        yield Env(app, svc, backend, rcon_srv)
        await engine.dispose()


def client(env: Env, **kw: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=BASE, **kw)  # type: ignore[arg-type]


async def login(c: httpx.AsyncClient) -> str:
    r = await c.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    return c.cookies["mcops_csrf"]


async def wait_state(srv: ServerController, st: State, tries: int = 80) -> None:
    for _ in range(tries):
        if srv.state is st:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"状态未到达 {st}，当前 {srv.state}")


async def test_health_is_public(env: Env) -> None:
    async with client(env) as c:
        r = await c.get("/api/health")
    assert r.json()["status"] == "ok"


async def test_requires_login(env: Env) -> None:
    async with client(env) as c:
        assert (await c.get("/api/server/status")).status_code == 401
        assert (await c.get("/api/auth/me")).status_code == 401


async def test_login_sets_httponly_session_and_me_works(env: Env) -> None:
    async with client(env) as c:
        r = await c.post("/api/auth/login", json={"password": PASSWORD})
        assert r.status_code == 200
        set_cookie = r.headers.get_list("set-cookie")
        session = next(h for h in set_cookie if h.startswith("mcops_session="))
        assert "HttpOnly" in session and "SameSite=strict" in session
        assert (await c.get("/api/auth/me")).json() == {"user": "admin"}


async def test_wrong_password_throttled_not_locked(env: Env) -> None:
    async with client(env) as c:
        # 连续失败只递增延迟、不拉黑；正确密码随后仍能登录成功（不会被锁死）
        for _ in range(5):
            r = await c.post("/api/auth/login", json={"password": "wrong"})
            assert r.status_code == 401
            assert r.json()["detail"] == "invalid_credentials"
        r = await c.post("/api/auth/login", json={"password": PASSWORD})
        assert r.status_code == 200


async def test_write_requires_csrf(env: Env) -> None:
    async with client(env) as c:
        csrf = await login(c)
        assert (await c.post("/api/server/start")).status_code == 403
        r = await c.post("/api/server/start", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 202 and r.json() == {"accepted": True}


async def test_rejects_foreign_host_and_origin(env: Env) -> None:
    async with client(env) as c:
        # DNS rebinding：Host 不是回环地址
        r = await c.get("/api/health", headers={"Host": "evil.example"})
        assert r.status_code == 421
        csrf = await login(c)
        r = await c.post(
            "/api/server/start",
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        )
        assert r.status_code == 403


async def test_start_ready_command_stop_flow(env: Env) -> None:
    srv = env.svc.server
    async with client(env) as c:
        csrf = await login(c)
        h = {"X-CSRF-Token": csrf}
        assert (await c.post("/api/server/start", headers=h)).status_code == 202
        assert srv.state is State.STARTING
        # 第二次启动冲突
        assert (await c.post("/api/server/start", headers=h)).status_code == 409
        # 命令在 running 之前被拒绝
        assert (
            await c.post("/api/server/command", json={"command": "list"}, headers=h)
        ).status_code == 409

        srv._ready.set()  # 模拟日志里出现 "Done ("
        await wait_state(srv, State.RUNNING)

        r = await c.post("/api/server/command", json={"command": "list"}, headers=h)
        assert r.json() == {"output": "ran:list"}

        env.backend.alive = True
        assert (await c.post("/api/server/stop", headers=h)).status_code == 202

        async def _died() -> None:
            await asyncio.sleep(0.2)
            env.backend.alive = False

        await _died()
        await wait_state(srv, State.STOPPED)
        assert "stop" in env.rcon.commands

        items = (await c.get("/api/events?limit=50")).json()["items"]
        types = [e["type"] for e in items]
        assert {"server_starting", "server_stop_requested", "server_stopped"} <= set(types)


async def test_start_fails_when_process_exits_before_ready(env: Env) -> None:
    srv = env.svc.server
    await srv.start()
    env.backend.alive = False  # 进程存活 ≠ 启动成功：就绪前退出应判为失败
    await wait_state(srv, State.CRASHED)
    evs = await env.svc.bus.recent(10, ["server_start_failed"])
    assert evs and evs[0].data["error"] == "进程在就绪前退出"


async def test_stop_kills_after_timeout(env: Env) -> None:
    srv = env.svc.server
    await srv.start()
    srv._ready.set()
    await wait_state(srv, State.RUNNING)
    await srv.stop()  # FakeBackend 不会自己退出 → 超时后强杀
    await wait_state(srv, State.STOPPED)
    assert env.backend.killed == 1
    assert srv.state is State.STOPPED


async def test_events_filter(env: Env) -> None:
    bus = env.svc.bus
    await bus.emit(Event("player_login", "mc_log", {"name": "a", "ip": "1.2.3.4"}))
    await bus.emit(Event("chat", "mc_log", {"name": "a", "message": "hi"}))
    async with client(env) as c:
        await login(c)
        items = (await c.get("/api/events?types=player_login")).json()["items"]
    assert [i["type"] for i in items] == ["player_login"]
    assert items[0]["data"]["ip"] == "1.2.3.4"


async def test_static_assets_cached_and_gzipped(tmp_path: Path) -> None:
    """哈希命名的 assets 永久缓存且 gzip；index.html 每次重新验证（部署后拿到新分块名）。"""

    from mcops.api import mount_static

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "index-abc.js").write_text("console.log(1);\n" * 500)
    app = FastAPI()
    mount_static(app, dist)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        r = await c.get("/assets/index-abc.js", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200
        assert r.headers["content-encoding"] == "gzip"
        assert "immutable" in r.headers["cache-control"]
        assert r.text.startswith("console.log(1);")
        r = await c.get("/assets/missing.js")
        assert r.status_code == 404 and "immutable" not in r.headers.get("cache-control", "")
        r = await c.get("/network")
        assert r.text == "<html>app</html>" and r.headers["cache-control"] == "no-cache"
