"""从旧 mc-qq-bot 导入数据：applications.json / members.json / groups.json / 审计与交互日志。

幂等：重复运行不会产生重复记录。只读旧文件，不修改。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db


def _load(p: Path) -> dict[str, Any]:
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


_AUDIT_RE = re.compile(
    r"^(?P<ts>\S+)\taction=(?P<action>[^\t]*)\tactor=(?P<actor>[^\t]*)\tplayer=(?P<player>[^\t]*)\tresult=(?P<result>.*)$"
)
_INTER_RE = re.compile(
    r"^(?P<ts>[\d-]+ [\d:,]+)\tgroup=(?P<group>[^\t]*)\topenid=(?P<openid>[^\t]*)\tadmin=(?P<admin>\w+)\t"
    r"cmd=(?P<cmd>[^\t]*)\tin=(?P<inp>[^\t]*)\tout=(?P<out>.*)$"
)


def _ts(raw: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S,%f"):
        try:
            t = datetime.strptime(raw, fmt)
            return (t if t.tzinfo else t.astimezone()).astimezone(UTC)
        except ValueError:
            continue
    return datetime.now(UTC)


async def import_legacy(engine: AsyncEngine, bot_dir: Path) -> dict[str, int]:
    counts = {"applications": 0, "members": 0, "groups": 0, "audit": 0, "interactions": 0}
    async with engine.begin() as conn:
        for name, rec in (_load(bot_dir / "applications.json").get("pending") or {}).items():
            stmt = sqlite_insert(db.bot_applications).values(
                name=name,
                openid=str(rec.get("openid", "")),
                ts=datetime.fromtimestamp(int(rec.get("ts", 0)), UTC),
            )
            r = await conn.execute(stmt.on_conflict_do_nothing(index_elements=["name"]))
            counts["applications"] += r.rowcount or 0
        for openid, names in (_load(bot_dir / "members.json").get("members") or {}).items():
            for n in names if isinstance(names, list) else []:
                stmt = sqlite_insert(db.bot_members).values(openid=openid, name=n, bound_at=None)
                r = await conn.execute(
                    stmt.on_conflict_do_nothing(index_elements=["openid", "name"])
                )
                counts["members"] += r.rowcount or 0
        for openid in _load(bot_dir / "groups.json").get("groups") or []:
            stmt = sqlite_insert(db.bot_groups).values(openid=openid, added_at=None)
            r = await conn.execute(stmt.on_conflict_do_nothing(index_elements=["openid"]))
            counts["groups"] += r.rowcount or 0

        # 日志只导入一次（以 kv 标记）
        done = (
            await conn.execute(
                select(db.kv.c.value).where(db.kv.c.key == "bot.legacy_logs_imported")
            )
        ).scalar()
        if not done:
            audit = bot_dir / "whitelist-audit.log"
            if audit.exists():
                rows = []
                for ln in audit.read_text(encoding="utf-8", errors="replace").splitlines():
                    if m := _AUDIT_RE.match(ln):
                        rows.append(
                            {
                                "ts": _ts(m["ts"]),
                                "actor": m["actor"],
                                "channel": "qq",
                                "action": f"whitelist.{m['action']}",
                                "target": m["player"],
                                "result": m["result"][:32],
                                "detail": {"legacy": True, "result": m["result"]},
                            }
                        )
                if rows:
                    await conn.execute(insert(db.audit_log), rows)
                counts["audit"] = len(rows)
            rows = []
            for p in sorted(bot_dir.glob("interactions.log*"), reverse=True):
                for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    if m := _INTER_RE.match(ln):
                        rows.append(
                            {
                                "ts": _ts(m["ts"]),
                                "group_openid": m["group"],
                                "openid": m["openid"] or None,
                                "admin": m["admin"] == "True",
                                "cmd": m["cmd"][:32],
                                "input": m["inp"],
                                "output": m["out"],
                                "simulated": False,
                            }
                        )
            if rows:
                await conn.execute(insert(db.bot_interactions), rows)
            counts["interactions"] = len(rows)
            await conn.execute(insert(db.kv).values(key="bot.legacy_logs_imported", value=True))
    return counts
