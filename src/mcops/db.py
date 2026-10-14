"""SQLite（WAL）存储：单一真相源。M0 用 create_all 建表，M1 起引入 Alembic 迁移。"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

metadata = MetaData()

events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False, index=True),
    Column("type", String(64), nullable=False, index=True),
    Column("source", String(64), nullable=False),
    Column("data", JSON, nullable=False),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("csrf", String(64), nullable=False),
    Column("user", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("client", String(128), nullable=False, default=""),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False, index=True),
    Column("actor", String(64), nullable=False),
    Column("channel", String(16), nullable=False),  # web / qq / cli / scheduler
    Column("action", String(64), nullable=False),
    Column("target", Text, nullable=False, default=""),
    Column("result", String(32), nullable=False),
    Column("detail", JSON, nullable=False, default=dict),
)

# ---- M1：玩家 / 会话 / IP / 告警 / 边缘采集 --------------------------------

player_sessions = Table(
    "player_sessions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(16), nullable=False, index=True),
    Column("login_at", DateTime(timezone=True), nullable=False, index=True),
    Column("logout_at", DateTime(timezone=True), nullable=True),
    Column("ip", String(64), nullable=True, index=True),
    Column("ip_trusted", Boolean, nullable=False, default=False),
    Column("port", Integer, nullable=True),
    Column("leave_reason", Text, nullable=True),
    Column("authenticated", Boolean, nullable=True),
    Column("carpet_client", Boolean, nullable=True),
    Column("fake", Boolean, nullable=False, default=False),
    Column("source", String(16), nullable=False, default="live"),  # live / backfill
)

frps_conns = Table(
    "frps_conns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False, index=True),
    Column("proxy", String(32), nullable=False),
    Column("ip", String(64), nullable=False, index=True),
    Column("port", Integer, nullable=True),
)

alerts = Table(
    "alerts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("first_ts", DateTime(timezone=True), nullable=False, index=True),
    Column("last_ts", DateTime(timezone=True), nullable=False),
    Column("rule", String(64), nullable=False, index=True),
    Column("severity", String(16), nullable=False),
    Column("title", Text, nullable=False),
    Column("detail", Text, nullable=False, default=""),
    Column("subject", String(128), nullable=True),
    Column("data", JSON, nullable=False, default=dict),
    Column("status", String(16), nullable=False, default="open", index=True),
    Column("acked_at", DateTime(timezone=True), nullable=True),
    Column("count", Integer, nullable=False, default=1),
    Column("historical", Boolean, nullable=False, default=False),
    Column("notified", Boolean, nullable=False, default=False),
)

kv = Table(
    "kv",
    metadata,
    Column("key", String(128), primary_key=True),
    Column("value", JSON, nullable=False),
)

# ---- M2：QQ 机器人 ----------------------------------------------------------

bot_groups = Table(
    "bot_groups",
    metadata,
    Column("openid", String(128), primary_key=True),
    Column("added_at", DateTime(timezone=True), nullable=True),
)

bot_applications = Table(
    "bot_applications",
    metadata,
    Column("name", String(16), primary_key=True),
    Column("openid", String(128), nullable=False, index=True),
    Column("ts", DateTime(timezone=True), nullable=False),
)

bot_members = Table(
    "bot_members",
    metadata,
    Column("openid", String(128), primary_key=True),
    Column("name", String(16), primary_key=True),
    Column("bound_at", DateTime(timezone=True), nullable=True),
)

# 经申请获批的白名单：期限内须进服并完成 EasyAuth 注册，否则自动移出
whitelist_grants = Table(
    "whitelist_grants",
    metadata,
    Column("name", String(16), primary_key=True),
    Column("openid", String(128), nullable=True),
    Column("granted_at", DateTime(timezone=True), nullable=False),
    Column("deadline", DateTime(timezone=True), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),  # pending/activated/expired/cancelled
    Column("resolved_at", DateTime(timezone=True), nullable=True),
)

bot_interactions = Table(
    "bot_interactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False, index=True),
    Column("group_openid", String(128), nullable=False),
    Column("openid", String(128), nullable=True),
    Column("admin", Boolean, nullable=False),
    Column("cmd", String(32), nullable=False),
    Column("input", Text, nullable=False),
    Column("output", Text, nullable=False),
    Column("simulated", Boolean, nullable=False, default=False),
)


def _set_sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


async def open_db(path: Path) -> AsyncEngine:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    sync_engine: Engine = engine.sync_engine
    event.listen(sync_engine, "connect", _set_sqlite_pragmas)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    # 库里有面板会话、SMTP 授权码等：只允许本用户读写（含 WAL / SHM 旁路文件）
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        with contextlib.suppress(FileNotFoundError):
            p.chmod(0o600)
    return engine


# ---- M3：运维任务 ------------------------------------------------------------

job_runs = Table(
    "job_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job", String(64), nullable=False, index=True),
    Column("trigger", String(32), nullable=False),  # schedule / web / cli
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("status", String(16), nullable=False),  # running / ok / failed
    Column("log", Text, nullable=False, default=""),
    Column("result", JSON, nullable=False, default=dict),
)

# ---- P1：面板配置 ------------------------------------------------------------

config_history = Table(
    "config_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False, index=True),
    Column("user", String(64), nullable=False),
    Column("action", String(16), nullable=False),  # save / reset / restore / import
    Column("changed", JSON, nullable=False, default=list),  # 改动的字段路径
    Column("overlay", JSON, nullable=False, default=dict),  # 保存后的完整覆盖层
    Column("note", String(200), nullable=False, default=""),
)
