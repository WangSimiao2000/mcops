"""备份 / restic 快照的限速下载。"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import AsyncIterator
from datetime import timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from mcops import db, download
from mcops.api import Services, create_app
from mcops.auth import Authenticator, hash_password
from mcops.backend import ObserveBackend
from mcops.config import ApiConfig, ServerConfig
from mcops.db import open_db
from mcops.events import EventBus
from mcops.players import ServerFiles
from mcops.rcon import RconClient
from mcops.server import ServerController
from mcops.supervisor import Supervisor

PW = "correct horse battery staple"
CST = timezone(timedelta(hours=8))
PAYLOAD = bytes(range(256)) * 400  # 100 KiB


class FakeRestic:
    repo = "/tmp/repo"

    def __init__(self) -> None:
        self.dumped: list[str] = []

    async def dump_zip(self, snapshot: str) -> asyncio.subprocess.Process:
        self.dumped.append(snapshot)
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'PK' * 5000)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Services, FakeRestic]]:
    game = tmp_path / "game"
    game.mkdir()
    bdir = tmp_path / "backups"
    bdir.mkdir()
    (bdir / "mc-backup-20261010_050504.tar.gz").write_bytes(PAYLOAD)
    (bdir / "mc-backup-20261010_060000.tar.gz.part").write_bytes(b"partial")
    engine = await open_db(tmp_path / "t.db")
    cfg = ServerConfig(game_dir=game, jar="server.jar")
    bus = EventBus(engine)
    server = ServerController(cfg, ObserveBackend(cfg), RconClient("127.0.0.1", 1, ""), bus, CST)
    svc = Services(
        ApiConfig(),
        engine,
        bus,
        server,
        Authenticator(engine, hash_password(PW), None, 3600),
        Supervisor(),
        ServerFiles(game, None),
    )
    restic = FakeRestic()
    svc.ops = SimpleNamespace(backup_cfg=SimpleNamespace(backup_dir=bdir), restic=restic)
    svc.download_rate_kib = 0
    app = create_app()
    app.state.svc = svc
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as c:
        assert (await c.post("/api/auth/login", json={"password": PW})).status_code == 200
        c.headers["X-CSRF-Token"] = c.cookies["mcops_csrf"]
        yield c, svc, restic
    download.release_slot()
    await engine.dispose()


def test_parse_range() -> None:
    assert download.parse_range(None, 100) is None
    assert download.parse_range("bytes=10-", 100) == (10, 99)
    assert download.parse_range("bytes=10-19", 100) == (10, 19)
    assert download.parse_range("bytes=-5", 100) == (95, 99)
    assert download.parse_range("bytes=90-500", 100) == (90, 99)
    for bad in ("bytes=100-", "bytes=5-1", "bytes=-", "items=0-1", "bytes=0-1,5-6"):
        with pytest.raises(HTTPException) as e:
            download.parse_range(bad, 100)
        assert e.value.status_code == 416


async def test_throttle_paces_bytes() -> None:
    t = download.Throttle(200 * 1024)
    t0 = time.monotonic()
    for _ in range(4):
        await t(25 * 1024)  # 100 KiB @ 200 KiB/s ≈ 0.5 s
    assert 0.4 <= time.monotonic() - t0 < 1.5
    t0 = time.monotonic()
    await download.Throttle(0)(10**9)  # 0 = 不限速
    assert time.monotonic() - t0 < 0.05


async def test_backup_download_full_range_and_audit(
    client: tuple[httpx.AsyncClient, Services, FakeRestic],
) -> None:
    c, svc, _ = client
    listing = (await c.get("/api/backups")).json()
    assert [b["name"] for b in listing["items"]] == ["mc-backup-20261010_050504.tar.gz"]
    assert listing["download_rate_kib"] == 0

    r = await c.get("/api/backups/mc-backup-20261010_050504.tar.gz/download")
    assert r.status_code == 200 and r.content == PAYLOAD
    assert r.headers["accept-ranges"] == "bytes"
    assert "attachment" in r.headers["content-disposition"]

    # 断点续传
    r = await c.get(
        "/api/backups/mc-backup-20261010_050504.tar.gz/download",
        headers={"Range": "bytes=1000-1999"},
    )
    assert r.status_code == 206 and r.content == PAYLOAD[1000:2000]
    assert r.headers["content-range"] == f"bytes 1000-1999/{len(PAYLOAD)}"

    # 只能下载列表里的完整备份：.part、路径穿越、不存在的文件都拒绝
    for bad in (
        "mc-backup-20261010_060000.tar.gz.part",
        "..%2F..%2Fsecrets.yaml",
        "nope.tar.gz",
    ):
        assert (await c.get(f"/api/backups/{bad}/download")).status_code == 404

    async with svc.engine.connect() as conn:
        rows = (await conn.execute(select(db.audit_log.c.action))).scalars().all()
    assert rows.count("backup.download") == 1  # 续传请求不重复记


async def test_only_one_download_at_a_time(
    client: tuple[httpx.AsyncClient, Services, FakeRestic],
) -> None:
    c, _, _ = client
    download.acquire_slot()
    r = await c.get("/api/backups/mc-backup-20261010_050504.tar.gz/download")
    assert r.status_code == 409
    download.release_slot()
    assert (
        await c.get("/api/backups/mc-backup-20261010_050504.tar.gz/download")
    ).status_code == 200
    # 完成后槽位已释放
    assert (
        await c.get("/api/backups/mc-backup-20261010_050504.tar.gz/download")
    ).status_code == 200


def test_stale_slot_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    download.acquire_slot()
    with pytest.raises(download.DownloadBusy):
        download.acquire_slot()
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + download.STALE_S + 1)
    download.acquire_slot()  # 心跳过期（客户端早早断开，生成器没启动）不会永久占用
    download.release_slot()


async def test_restic_snapshot_download(
    client: tuple[httpx.AsyncClient, Services, FakeRestic],
) -> None:
    c, _, restic = client
    r = await c.get("/api/restic/0a1b2c3d/download")
    assert r.status_code == 200 and r.content == b"PK" * 5000
    assert "mc-snapshot-0a1b2c3d.zip" in r.headers["content-disposition"]
    assert restic.dumped == ["0a1b2c3d"]
    assert (await c.get("/api/restic/latest;rm/download")).status_code == 400
    assert (await c.get("/api/restic/ZZZZZZZZ/download")).status_code == 400
