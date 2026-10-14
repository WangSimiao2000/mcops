"""进程内事件总线：结构化事件持久化到 SQLite 并广播给订阅者（WebSocket、规则引擎、机器人）。

取代旧系统里 `.watchdog/state`、`maintenance.signal` 等跨进程文本契约。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Event:
    type: str
    source: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "type": self.type,
            "source": self.source,
            "data": self.data,
        }


class Broadcaster[T]:
    """有界队列扇出。慢订阅者丢弃最旧消息而不是拖慢发布方。"""

    def __init__(self, maxsize: int = 1000) -> None:
        self._subs: set[asyncio.Queue[T]] = set()
        self._maxsize = maxsize

    def publish(self, item: T) -> None:
        for q in list(self._subs):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(item)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[T]]:
        q: asyncio.Queue[T] = asyncio.Queue(self._maxsize)
        self._subs.add(q)
        try:
            yield q
        finally:
            self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


class EventBus:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self.events: Broadcaster[Event] = Broadcaster()

    async def emit(self, ev: Event) -> Event:
        async with self._engine.begin() as conn:
            res = await conn.execute(
                insert(db.events).values(ts=ev.ts, type=ev.type, source=ev.source, data=ev.data)
            )
            pk = res.inserted_primary_key
            ev.id = int(pk[0]) if pk else None
        self.events.publish(ev)
        return ev

    async def recent(self, limit: int = 100, types: Sequence[str] | None = None) -> list[Event]:
        q = select(db.events).order_by(desc(db.events.c.id)).limit(max(1, min(limit, 1000)))
        if types:
            q = q.where(db.events.c.type.in_(list(types)))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        out = []
        for r in rows:
            ts: datetime = r["ts"]
            if ts.tzinfo is None:  # SQLite 不保存时区；写入时统一为 UTC
                ts = ts.replace(tzinfo=UTC)
            out.append(Event(id=r["id"], ts=ts, type=r["type"], source=r["source"], data=r["data"]))
        return out

    async def recent_for_name(self, name: str, limit: int = 100) -> list[Event]:
        """按事件 data.name 在 SQL 侧过滤（不受"最近 N 条"窗口影响，避免高频事件把目标玩家挤掉）。"""
        name_expr = func.lower(func.json_extract(db.events.c.data, "$.name"))
        q = (
            select(db.events)
            .where(name_expr == name.lower())
            .order_by(desc(db.events.c.id))
            .limit(max(1, min(limit, 1000)))
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        out = []
        for r in rows:
            ts: datetime = r["ts"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            out.append(Event(id=r["id"], ts=ts, type=r["type"], source=r["source"], data=r["data"]))
        return out
