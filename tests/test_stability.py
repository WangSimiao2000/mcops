"""稳定性整改回归测试：备份排序、保留期清理、Supervisor 收尾。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import insert, select

from mcops import db
from mcops.backup import list_backups
from mcops.config import RetentionConfig
from mcops.db import open_db
from mcops.retention import prune_once
from mcops.supervisor import Supervisor


def test_list_backups_sorts_by_filename_not_mtime(tmp_path: Path) -> None:
    import os
    import time

    d = tmp_path / "bk"
    d.mkdir()
    old = d / "mc-backup-20260101_050000.tar.gz"
    new = d / "mc-backup-20260110_050000.tar.gz"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    # 把"旧"文件的 mtime 改成比"新"文件更晚（模拟 touch/拷贝/时钟回拨）
    now = time.time()
    os.utime(new, (now - 10000, now - 10000))
    os.utime(old, (now, now))
    assert [p.name for p in list_backups(d)] == [new.name, old.name]  # 仍按文件名时间排序


async def test_prune_once_deletes_old_rows(tmp_path: Path) -> None:
    engine = await open_db(tmp_path / "t.db")
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        for age_days in (400, 10):
            await conn.execute(
                insert(db.events).values(
                    ts=now - timedelta(days=age_days), type="x", source="s", data={}
                )
            )
    removed = await prune_once(
        engine, RetentionConfig(events_days=365, job_runs_days=0, bot_interactions_days=0), None
    )
    assert removed["events"] == 1  # 只删 400 天前那条
    async with engine.connect() as conn:
        left = (await conn.execute(select(db.events))).all()
    assert len(left) == 1
    await engine.dispose()


async def test_prune_zero_days_keeps_everything(tmp_path: Path) -> None:
    engine = await open_db(tmp_path / "t.db")
    async with engine.begin() as conn:
        await conn.execute(
            insert(db.events).values(
                ts=datetime.now(UTC) - timedelta(days=9999), type="x", source="s", data={}
            )
        )
    # events_days=0 表示永久保留
    removed = await prune_once(engine, RetentionConfig(events_days=0), None)
    assert removed.get("events", 0) == 0
    await engine.dispose()


async def test_supervisor_stop_all_cancels_and_awaits() -> None:
    sup = Supervisor()
    cleaned = []

    async def forever() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cleaned.append("resident")
            raise

    async def oneshot() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cleaned.append("oneshot")  # 收尾（例如 save-on）必须跑到
            raise

    sup.spawn("resident", forever)
    sup.run_once("oneshot", oneshot())
    await asyncio.sleep(0.05)
    await sup.stop_all(grace_s=0.1)
    assert set(cleaned) == {"resident", "oneshot"}
