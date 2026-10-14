"""日志页后端测试：解析、过滤、tail/分页、非法正则、跨文件搜索与上限、路径穿越、journal。"""

from __future__ import annotations

import asyncio
import gzip
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from mcops import logs
from mcops.api import Services, create_app
from mcops.auth import Authenticator, hash_password
from mcops.backend import ObserveBackend
from mcops.config import ServerConfig
from mcops.db import open_db
from mcops.events import EventBus
from mcops.players import ServerFiles
from mcops.rcon import RconClient
from mcops.server import ServerController
from mcops.supervisor import Supervisor

PW = "correct horse battery staple"
CST = timezone(timedelta(hours=8))

# 一个跨午夜回跳 + 多行异常堆栈的 latest.log。
LATEST = """\
[23:59:58] [Server thread/INFO]: Done (1.0s)! For help, type "help"
[23:59:59] [Server thread/WARN]: Can't keep up! Is the server overloaded?
[00:00:01] [Server thread/ERROR]: Encountered an unexpected exception
java.lang.RuntimeException: boom
\tat net.minecraft.Foo.bar(Foo.java:42)
\tat net.minecraft.Baz.qux(Baz.java:7)
[00:00:02] [Server thread/INFO]: recovered and continuing
"""

GZ_BODY = """\
[10:00:00] [Server thread/INFO]: older line one
[10:00:01] [Server thread/WARN]: needle in the haystack
[10:00:02] [Server thread/ERROR]: boom in archive
"""


def _write_logs(game: Path) -> None:
    logs_dir = game / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "latest.log").write_text(LATEST, encoding="utf-8")
    with gzip.open(logs_dir / "2026-10-07-1.log.gz", "wt", encoding="utf-8") as fh:
        fh.write(GZ_BODY)
    with gzip.open(logs_dir / "2026-10-06-1.log.gz", "wt", encoding="utf-8") as fh:
        fh.write("[09:00:00] [Server thread/INFO]: ancient line\n")
    crash = game / "crash-reports"
    crash.mkdir()
    (crash / "crash-2026-10-07_10.00.00-server.txt").write_text("the crash\n", encoding="utf-8")


@pytest.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    game = tmp_path / "game"
    game.mkdir()
    _write_logs(game)
    engine = await open_db(tmp_path / "t.db")
    bus = EventBus(engine)
    cfg = ServerConfig(game_dir=game, jar="server.jar")
    server = ServerController(
        cfg, ObserveBackend(cfg), RconClient("127.0.0.1", 1, ""), bus, CST, observe=True
    )
    from mcops.config import ApiConfig

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
    from mcops import api_logs

    api_logs.register(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as c:
        r = await c.post("/api/auth/login", json={"password": PW})
        assert r.status_code == 200
        c.headers["X-CSRF-Token"] = c.cookies["mcops_csrf"]
        yield c, svc
    await engine.dispose()


# ---- 纯函数：解析、跨午夜、continuation ------------------------------------


def test_parse_cross_midnight_and_continuation(tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "logs").mkdir(parents=True)
    (game / "logs" / "latest.log").write_text(LATEST, encoding="utf-8")
    path = logs.resolve_game_log(game, "latest.log")
    res = logs.read_view(
        path,
        logs.file_date_for(game, "latest.log"),
        CST,
        tail=None,
        offset=None,
        limit=None,
        levels=None,
        q=None,
        rx=None,
    )
    lines = res["lines"]
    # 三行无前缀（异常头 + 两行堆栈）=> continuation
    conts = [ln for ln in lines if ln["continuation"]]
    assert len(conts) == 3
    assert conts[0]["text"].startswith("java.lang.RuntimeException")
    # 跨午夜：23:59 的那天与 00:00 的那天相差一天
    first = next(ln for ln in lines if ln["time"] and ln["text"].startswith("Done"))
    recovered = next(ln for ln in lines if ln["text"] == "recovered and continuing")
    d0 = datetime.fromisoformat(first["time"]).date()
    d1 = datetime.fromisoformat(recovered["time"]).date()
    assert d1 == d0 + timedelta(days=1)


def test_level_and_keyword_filter_keeps_continuation(tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "logs").mkdir(parents=True)
    (game / "logs" / "latest.log").write_text(LATEST, encoding="utf-8")
    path = logs.resolve_game_log(game, "latest.log")
    # 只要 ERROR：命中那条 + 它的两行堆栈 continuation 一起保留
    res = logs.read_view(
        path,
        logs.file_date_for(game, "latest.log"),
        CST,
        tail=None,
        offset=None,
        limit=None,
        levels={"ERROR"},
        q=None,
        rx=None,
    )
    texts = [ln["text"] for ln in res["lines"]]
    assert "Encountered an unexpected exception" in texts
    assert any(t.startswith("java.lang.RuntimeException") for t in texts)
    assert "recovered and continuing" not in texts
    # 关键词
    res2 = logs.read_view(
        path,
        logs.file_date_for(game, "latest.log"),
        CST,
        tail=None,
        offset=None,
        limit=None,
        levels=None,
        q="overloaded",
        rx=None,
    )
    assert len(res2["lines"]) == 1 and "overloaded" in res2["lines"][0]["text"]


def test_tail_and_paging(tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "logs").mkdir(parents=True)
    body = "".join(f"[10:00:{i:02d}] [Server thread/INFO]: line {i}\n" for i in range(30))
    (game / "logs" / "latest.log").write_text(body, encoding="utf-8")
    path = logs.resolve_game_log(game, "latest.log")
    fd = logs.file_date_for(game, "latest.log")
    tailed = logs.read_view(
        path, fd, CST, tail=5, offset=None, limit=None, levels=None, q=None, rx=None
    )
    assert [ln["text"] for ln in tailed["lines"]] == [f"line {i}" for i in range(25, 30)]
    assert tailed["truncated"] is True
    paged = logs.read_view(
        path, fd, CST, tail=None, offset=10, limit=5, levels=None, q=None, rx=None
    )
    assert [ln["text"] for ln in paged["lines"]] == [f"line {i}" for i in range(10, 15)]
    assert paged["total"] == 30


def test_gz_is_read(tmp_path: Path) -> None:
    game = tmp_path / "game"
    _write_logs(game)
    path = logs.resolve_game_log(game, "2026-10-07-1.log.gz")
    res = logs.read_view(
        path,
        logs.file_date_for(game, "2026-10-07-1.log.gz"),
        CST,
        tail=None,
        offset=None,
        limit=None,
        levels=None,
        q="needle",
        rx=None,
    )
    assert len(res["lines"]) == 1 and "needle" in res["lines"][0]["text"]


# ---- 正则 -----------------------------------------------------------------


def test_bad_regex_raises_422() -> None:
    with pytest.raises(logs.LogsError) as ei:
        logs.compile_regex("(unclosed")
    assert ei.value.status == 422
    with pytest.raises(logs.LogsError) as ei2:
        logs.compile_regex("x" * (logs.REGEX_MAX_LEN + 1))
    assert ei2.value.status == 422


async def test_view_endpoint_bad_regex_422(ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = ctx
    r = await c.get("/api/logs/view", params={"source": "latest.log", "q": "(bad", "regex": "true"})
    assert r.status_code == 422


# ---- 跨文件搜索 ------------------------------------------------------------


def test_search_across_files(tmp_path: Path) -> None:
    game = tmp_path / "game"
    _write_logs(game)
    res = logs.search_files(
        game,
        CST,
        q="needle",
        date_from=__import__("datetime").date(2026, 10, 1),
        date_to=__import__("datetime").date(2026, 10, 31),
        levels=None,
        rx=None,
    )
    assert res["partial"] is False
    assert len(res["hits"]) == 1
    assert res["hits"][0]["source"] == "2026-10-07-1.log.gz"


def test_search_hits_capped_at_500(tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "logs").mkdir(parents=True)
    body = "".join(f"[10:00:00] [Server thread/INFO]: hit {i}\n" for i in range(800))
    (game / "logs" / "2026-10-07-1.log").write_text(body, encoding="utf-8")
    res = logs.search_files(
        game,
        CST,
        q="hit",
        date_from=__import__("datetime").date(2026, 10, 1),
        date_to=__import__("datetime").date(2026, 10, 31),
        levels=None,
        rx=None,
    )
    assert len(res["hits"]) == logs.SEARCH_HITS_MAX


async def test_search_endpoint_default_range(ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = ctx
    # 默认最近 7 天，needle 在 10-07；用显式范围保证命中与环境日期无关。
    r = await c.get(
        "/api/logs/search", params={"q": "needle", "from": "2026-10-01", "to": "2026-10-31"}
    )
    assert r.status_code == 200
    assert any(h["source"] == "2026-10-07-1.log.gz" for h in r.json()["hits"])
    # 范围超 60 天 => 422
    r2 = await c.get(
        "/api/logs/search", params={"q": "x", "from": "2026-01-01", "to": "2026-12-31"}
    )
    assert r2.status_code == 422


# ---- 路径穿越 --------------------------------------------------------------


def test_path_traversal_rejected(tmp_path: Path) -> None:
    game = tmp_path / "game"
    _write_logs(game)
    (tmp_path / "secret.log").write_text("top secret\n", encoding="utf-8")
    for bad in ("../secret.log", "/etc/passwd", "..%2fsecret.log", "latest.log/../../secret.log"):
        with pytest.raises(logs.LogsError) as ei:
            logs.resolve_game_log(game, bad)
        assert ei.value.status in (400, 404)


def test_symlink_outside_rejected(tmp_path: Path) -> None:
    game = tmp_path / "game"
    _write_logs(game)
    (tmp_path / "outside.log").write_text("outside\n", encoding="utf-8")
    link = game / "logs" / "2026-10-05-1.log"
    try:
        os.symlink(tmp_path / "outside.log", link)
    except OSError:
        pytest.skip("symlink not permitted")
    with pytest.raises(logs.LogsError) as ei:
        logs.resolve_game_log(game, "2026-10-05-1.log")
    assert ei.value.status == 400


async def test_download_rejects_journal(ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = ctx
    r = await c.get("/api/logs/download", params={"source": "journal:agent"})
    assert r.status_code == 400
    r2 = await c.get("/api/logs/download", params={"source": "latest.log"})
    assert r2.status_code == 200
    assert "Done" in r2.text


# ---- journal ---------------------------------------------------------------


async def test_journal_reads_via_subprocess(
    ctx: tuple[httpx.AsyncClient, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _ = ctx
    sample = (
        "2026-10-09T17:50:31+0800 host mcops-agent[1]: starting up\n"
        "2026-10-09T17:50:32+0800 host mcops-agent[1]: WARN low memory\n"
        "2026-10-09T17:50:33+0800 host mcops-agent[1]: ERROR crashed\n"
    )

    class FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return sample.encode(), b""

    async def fake_exec(*args: str, **kwargs: object) -> FakeProc:
        assert "journalctl" in args[0]
        assert "mcops-agent.service" in args
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    r = await c.get("/api/logs/view", params={"source": "journal:agent", "tail": 100})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    levels = [ln["level"] for ln in data["lines"]]
    assert levels == ["INFO", "WARN", "ERROR"]
    assert data["lines"][0]["text"] == "starting up"


async def test_journal_missing_binary_friendly_error(
    ctx: tuple[httpx.AsyncClient, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _ = ctx

    async def boom(*args: str, **kwargs: object) -> object:
        raise FileNotFoundError("journalctl")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    r = await c.get("/api/logs/view", params={"source": "journal:server"})
    assert r.status_code == 503
    assert "journalctl" in r.json()["detail"]


async def test_sources_endpoint(ctx: tuple[httpx.AsyncClient, Services]) -> None:
    c, _ = ctx
    r = await c.get("/api/logs/sources")
    assert r.status_code == 200
    data = r.json()
    names = [g["name"] for g in data["game"]]
    assert names[0] == "latest.log"  # latest 最前
    assert "2026-10-07-1.log.gz" in names
    assert len(data["crash"]) == 1
    assert {j["name"] for j in data["journal"]} == {"agent", "server"}
