"""本地数据库按保留期清理：防止 events / frps_conns 等表无限增长。

审计日志、告警、玩家会话、白名单记录不清理：体积小，而且是复盘依据。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Column, Table, delete
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.config import RetentionConfig

log = logging.getLogger(__name__)

FIRST_DELAY_S = 600.0  # 启动 10 分钟后第一次清理，避开开机时的回填和接管
INTERVAL_S = 24 * 3600.0


async def prune_once(
    engine: AsyncEngine,
    cfg: RetentionConfig,
    frps_days: int | None,
    now: datetime | None = None,
) -> dict[str, int]:
    """删除超过保留期的行，返回每张表删除的行数。"""
    now = now or datetime.now(UTC)
    plan: list[tuple[str, Table, Column[datetime], int | None]] = [
        ("events", db.events, db.events.c.ts, cfg.events_days),
        ("bot_interactions", db.bot_interactions, db.bot_interactions.c.ts, cfg.bot_interactions_days),
        ("job_runs", db.job_runs, db.job_runs.c.started_at, cfg.job_runs_days),
        ("frps_conns", db.frps_conns, db.frps_conns.c.ts, frps_days),
        ("auth_sessions", db.auth_sessions, db.auth_sessions.c.expires_at, 0),
    ]  # fmt: skip
    removed: dict[str, int] = {}
    async with engine.begin() as conn:
        for name, table, col, days in plan:
            if days is None or (days == 0 and name != "auth_sessions"):
                continue
            # auth_sessions 的"保留期 0"表示删掉已过期的会话
            res = await conn.execute(delete(table).where(col < now - timedelta(days=days)))
            removed[name] = int(res.rowcount or 0)
    return removed


async def retention_loop(
    engine: AsyncEngine,
    cfg: RetentionConfig,
    frps_days: int | None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    await sleep(FIRST_DELAY_S)
    while True:
        removed = await prune_once(engine, cfg, frps_days)
        if any(removed.values()):
            log.info("数据保留清理：%s", removed)
        await sleep(INTERVAL_S)
