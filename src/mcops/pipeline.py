"""事件处理管线：事件总线 → 玩家会话追踪 → 安全规则。实时与历史回填共用同一套逻辑。"""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db, logparse
from mcops.events import Event, EventBus
from mcops.players import PlayerTracker, ServerFiles
from mcops.security import SecurityEngine

log = logging.getLogger(__name__)

# 只有这些事件需要经过会话追踪与规则引擎
_RELEVANT = {
    "player_login",
    "player_leave",
    "carpet_client",
    "auth_success",
    "auth_attempt",
    "auth_failure",
    "auth_register",
    "anticheat",
    "frps_conn",
    "login_rejected",
    "server_stopping",
    "server_stopped",
    "server_crashed",
    "server_observed_down",
}
# 不涉及会话的事件直接交给规则引擎
_NO_SESSION = {"frps_conn", "login_rejected"}


class Pipeline:
    def __init__(self, tracker: PlayerTracker, security: SecurityEngine) -> None:
        self.tracker, self.security = tracker, security

    async def process(self, ev: Event) -> None:
        if ev.type not in _RELEVANT:
            return
        change = None if ev.type in _NO_SESSION else await self.tracker.handle(ev)
        await self.security.handle(ev, change)


async def run_live(bus: EventBus, pipeline: Pipeline) -> None:
    async with bus.events.subscribe() as q:
        while True:
            ev = await q.get()
            try:
                await pipeline.process(ev)
            except Exception:
                log.exception("处理事件失败：%s", ev.type)


async def run_ticks(security: SecurityEngine, interval_s: float = 5.0) -> None:
    while True:
        await asyncio.sleep(interval_s)
        await security.tick(datetime.now(UTC))


async def run_whitelist_audit(
    security: SecurityEngine, files: ServerFiles, interval_s: float = 600.0
) -> None:
    while True:
        await security.check_unregistered(
            files.whitelist(), files.easyauth_registered(), datetime.now(UTC)
        )
        await asyncio.sleep(interval_s)


# ---- 历史回填 --------------------------------------------------------------

_LOG_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})-(\d+)\.log(?:\.gz)?$")


def _sort_key(p: Path) -> tuple[str, int]:
    m = _LOG_NAME.search(p.name)
    if m:
        return m.group(1), int(m.group(2))
    return "9999-99-99", 0  # latest.log 最后处理


def iter_log_events(paths: Iterable[Path], tz: tzinfo, today: date) -> Iterable[Event]:
    for p in sorted(paths, key=_sort_key):
        m = _LOG_NAME.search(p.name)
        start = date.fromisoformat(m.group(1)) if m else today
        opener = gzip.open if p.suffix == ".gz" else open
        tracker = logparse.DayTracker(start)
        last_ts: datetime | None = None
        with opener(p, "rt", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = logparse.parse_line(raw, tracker.feed(raw), tz)
                if line is None:
                    continue
                last_ts = line.ts.astimezone(UTC)
                for pe in logparse.classify(line):
                    yield Event(pe.type, "backfill", pe.data, ts=last_ts)
        # 每个日志文件对应一次服务端运行；文件结束视为停服，关闭未结束的会话
        if last_ts is not None and m is not None:
            yield Event("server_stopped", "backfill", {"file": p.name}, ts=last_ts)


async def backfill(
    engine: AsyncEngine,
    pipeline: Pipeline,
    paths: list[Path],
    tz: tzinfo,
) -> dict[str, int]:
    """把历史日志写入 events（source=backfill）与 player_sessions，并按历史模式跑规则。"""
    prev_hist, prev_source = pipeline.security.historical, pipeline.tracker.source
    pipeline.security.historical, pipeline.tracker.source = True, "backfill"
    try:
        return await _backfill(engine, pipeline, paths, tz)
    finally:
        pipeline.security.historical, pipeline.tracker.source = prev_hist, prev_source


async def _backfill(
    engine: AsyncEngine, pipeline: Pipeline, paths: list[Path], tz: tzinfo
) -> dict[str, int]:
    counts: dict[str, int] = {}
    batch: list[dict[str, object]] = []
    today = datetime.now(tz).date()
    for ev in iter_log_events(paths, tz, today):
        counts[ev.type] = counts.get(ev.type, 0) + 1
        if ev.type not in ("server_stopped",):
            batch.append({"ts": ev.ts, "type": ev.type, "source": ev.source, "data": ev.data})
        await pipeline.process(ev)
        await pipeline.security.tick(ev.ts)
        if len(batch) >= 500:
            async with engine.begin() as conn:
                await conn.execute(insert(db.events), batch)
            batch.clear()
    if batch:
        async with engine.begin() as conn:
            await conn.execute(insert(db.events), batch)
    await pipeline.security.tick(datetime.now(UTC) + timedelta(minutes=1))
    return counts
