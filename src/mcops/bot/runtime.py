"""机器人运行时：组装 BotCore + QQ 客户端，供 main 托管、API 查询。"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.bot.core import BotCore
from mcops.bot.qq import QQClient, status_json

log = logging.getLogger(__name__)


class BotRuntime:
    def __init__(
        self, engine: AsyncEngine, core: BotCore, client: QQClient | None, enabled: bool
    ) -> None:
        self.engine, self.core, self.client, self.enabled = engine, core, client, enabled

    async def status(self) -> dict[str, Any]:
        return status_json(self.client, self.core, await self.core.groups(), self.enabled)

    async def members_by_name(self) -> dict[str, list[str]]:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(select(db.bot_members.c.name, db.bot_members.c.openid))
            ).all()
        out: dict[str, list[str]] = defaultdict(list)
        for name, openid in rows:
            out[name.lower()].append(openid)
        return out

    async def on_alert(self, alert: dict[str, Any]) -> None:
        if self.enabled and self.client is not None and self.client.connected:
            try:
                await self.core.push_security_alert(alert)
            except Exception:
                log.exception("QQ 安全告警推送失败")
