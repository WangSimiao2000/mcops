"""玩家会话与 IP 画像：把 player_login / carpet_client / auth_success / player_leave 等事件
聚合成会话（player_sessions 表），并提供查询。

IP 可信度：2026-10-09 启用 PROXY protocol 之前，所有连接在服务端看来都是 127.0.0.1，
这类记录标记为 ip_trusted=False，安全规则不会拿它当"已知 IP"。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, desc, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.events import Event

log = logging.getLogger(__name__)


def ip_trusted(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_loopback or a.is_unspecified)


def ip_prefix(ip: str) -> str:
    """同一运营商小段内换 IP 很常见（家宽每天变），新 IP 判定按 /24（IPv6 按 /48）。"""
    a = ipaddress.ip_address(ip)
    net = ipaddress.ip_network(f"{ip}/{24 if a.version == 4 else 48}", strict=False)
    return str(net)


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


@dataclass(slots=True)
class SessionChange:
    """tracker 处理一个事件后的结果，供安全规则使用。"""

    kind: str  # opened / closed / updated
    session: dict[str, Any]


class PlayerTracker:
    def __init__(self, engine: AsyncEngine, source: str = "live") -> None:
        self.engine = engine
        self.source = source

    async def _open_session(self, conn: Any, name: str) -> dict[str, Any] | None:
        row = (
            (
                await conn.execute(
                    select(db.player_sessions)
                    .where(
                        and_(
                            db.player_sessions.c.name == name,
                            db.player_sessions.c.logout_at.is_(None),
                        )
                    )
                    .order_by(desc(db.player_sessions.c.id))
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def handle(self, ev: Event) -> SessionChange | None:
        d = ev.data
        async with self.engine.begin() as conn:
            if ev.type == "player_login":
                name = d["name"]
                prev = await self._open_session(conn, name)
                if prev is not None:  # 漏掉了离开事件（崩溃等）
                    await conn.execute(
                        update(db.player_sessions)
                        .where(db.player_sessions.c.id == prev["id"])
                        .values(logout_at=ev.ts, leave_reason="superseded")
                    )
                row = {
                    "name": name,
                    "login_at": ev.ts,
                    "ip": d.get("ip"),
                    "ip_trusted": ip_trusted(d.get("ip")),
                    "port": d.get("port"),
                    "fake": bool(d.get("fake")),
                    "source": self.source,
                }
                res = await conn.execute(insert(db.player_sessions).values(**row))
                row["id"] = int(res.inserted_primary_key[0])  # type: ignore[index]
                row.update(
                    logout_at=None, leave_reason=None, authenticated=None, carpet_client=None
                )
                return SessionChange("opened", row)

            if ev.type in ("carpet_client", "auth_success", "auth_attempt"):
                sess = await self._open_session(conn, d["name"])
                if sess is None:
                    return None
                if ev.type == "auth_attempt":
                    # 尝试过 /login 但还没成功：先记为 False，成功后改 True
                    if sess["authenticated"] is not None:
                        return None
                    col, val = "authenticated", False
                else:
                    col = "carpet_client" if ev.type == "carpet_client" else "authenticated"
                    val = True
                await conn.execute(
                    update(db.player_sessions)
                    .where(db.player_sessions.c.id == sess["id"])
                    .values({col: val})
                )
                sess[col] = val
                return SessionChange("updated", sess)

            if ev.type == "player_leave":
                sess = await self._open_session(conn, d["name"])
                if sess is None:
                    return None
                vals: dict[str, Any] = {"logout_at": ev.ts, "leave_reason": d.get("reason")}
                if sess["carpet_client"] is None:
                    vals["carpet_client"] = False
                # EasyAuth 只有开启 log-player-login 后才记录认证（生产 10-08 起），
                # 所以没有任何认证记录时保持"未知"；被认证超时踢出则确定为失败
                if "Authentication time has expired" in (d.get("reason") or ""):
                    vals["authenticated"] = False
                await conn.execute(
                    update(db.player_sessions)
                    .where(db.player_sessions.c.id == sess["id"])
                    .values(**vals)
                )
                sess.update(vals)
                return SessionChange("closed", sess)

            if ev.type in (
                "server_stopping",
                "server_stopped",
                "server_crashed",
                "server_observed_down",
            ):
                await conn.execute(
                    update(db.player_sessions)
                    .where(db.player_sessions.c.logout_at.is_(None))
                    .values(logout_at=ev.ts, leave_reason=f"<{ev.type}>")
                )
        return None


# ---- 查询 -----------------------------------------------------------------


def _iso(ts: datetime | None) -> str | None:
    a = _aware(ts)
    return a.isoformat() if a is not None else None


def _session_json(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "login_at": _iso(r["login_at"]),
        "logout_at": _iso(r["logout_at"]),
        "ip": r["ip"],
        "ip_trusted": bool(r["ip_trusted"]),
        "port": r["port"],
        "leave_reason": r["leave_reason"],
        "authenticated": r["authenticated"],
        "carpet_client": r["carpet_client"],
        "fake": bool(r["fake"]),
    }


class ServerFiles:
    """只读访问服务端文件：whitelist.json、ops.json、EasyAuth 数据库。"""

    def __init__(self, game_dir: Path, easyauth_db: Path | None) -> None:
        self.game_dir = game_dir
        self.easyauth_db = easyauth_db

    def _names(self, fname: str) -> list[str]:
        try:
            data = json.loads((self.game_dir / fname).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return [e["name"] for e in data if isinstance(e, dict) and "name" in e]

    def whitelist(self) -> list[str]:
        return self._names("whitelist.json")

    def ops(self) -> set[str]:
        return {n.lower() for n in self._names("ops.json")}

    def banned_ips(self) -> set[str]:
        try:
            data = json.loads((self.game_dir / "banned-ips.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return set()
        return {e["ip"] for e in data if isinstance(e, dict) and "ip" in e}

    def easyauth_registered(self) -> set[str] | None:
        """返回已设置密码的用户名（小写）；读不到数据库时返回 None（未知）。

        以只读 + immutable 方式打开，不影响正在运行的服务端。
        """
        if self.easyauth_db is None or not self.easyauth_db.exists():
            return None
        try:
            uri = f"file:{self.easyauth_db}?mode=ro&immutable=1"
            with sqlite3.connect(uri, uri=True) as c:
                rows = c.execute("select username_lower, data from easyauth").fetchall()
        except sqlite3.Error as e:
            log.warning("读取 EasyAuth 数据库失败：%s", e)
            return None
        out = set()
        for name, data in rows:
            try:
                if json.loads(data).get("password"):
                    out.add(str(name).lower())
            except (TypeError, json.JSONDecodeError):
                continue
        return out


async def list_players(
    engine: AsyncEngine, files: ServerFiles, q: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    s = db.player_sessions.c
    agg = (
        select(
            s.name,
            func.min(s.login_at).label("first_seen"),
            func.max(func.coalesce(s.logout_at, s.login_at)).label("last_seen"),
            func.count().label("sessions"),
            func.count(func.distinct(s.ip)).filter(s.ip_trusted.is_(True)).label("distinct_ips"),
            func.sum(func.iif(s.logout_at.is_(None), 1, 0)).label("open_sessions"),
        )
        .where(s.fake.is_(False))
        .group_by(s.name)
    )
    async with engine.connect() as conn:
        rows: dict[str, dict[str, Any]] = {
            r["name"].lower(): dict(r) for r in (await conn.execute(agg)).mappings()
        }
        last_ip_rows = (
            (
                await conn.execute(
                    select(s.name, s.ip, s.ip_trusted)
                    .where(s.fake.is_(False))
                    .order_by(s.name, desc(s.id))
                )
            )
            .mappings()
            .all()
        )
    last_ip: dict[str, tuple[str | None, bool]] = {}
    for lr in last_ip_rows:
        last_ip.setdefault(lr["name"].lower(), (lr["ip"], bool(lr["ip_trusted"])))

    wl = files.whitelist()
    ops = files.ops()
    reg = files.easyauth_registered()
    names: dict[str, str] = {n.lower(): n for n in wl}
    for k, row in rows.items():
        names.setdefault(k, row["name"])

    out = []
    wl_lower = {n.lower() for n in wl}
    for key, display in names.items():
        if q and q.lower() not in key:
            continue
        r: dict[str, Any] = rows.get(key, {})
        ip, trusted = last_ip.get(key, (None, False))
        registered = None if reg is None else key in reg
        flags = []
        if key in wl_lower and registered is False:
            flags.append("unregistered_whitelisted")
        out.append(
            {
                "name": display,
                "first_seen": _iso(r.get("first_seen")),
                "last_seen": _iso(r.get("last_seen")),
                "sessions": int(r.get("sessions") or 0),
                "last_ip": ip,
                "last_ip_trusted": trusted,
                "distinct_ips": int(r.get("distinct_ips") or 0),
                "online": int(r.get("open_sessions") or 0) > 0,
                "whitelisted": key in wl_lower,
                "easyauth_registered": registered,
                "is_op": key in ops,
                "flags": flags,
            }
        )
    # 有标记在前；其余按最后上线倒序
    flagged = [p for p in out if p["flags"]]
    rest = sorted(
        (p for p in out if not p["flags"]), key=lambda p: p["last_seen"] or "", reverse=True
    )
    return (flagged + rest)[:limit]


async def player_detail(
    engine: AsyncEngine, name: str, limit: int = 200
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    s = db.player_sessions.c
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    select(db.player_sessions)
                    .where(func.lower(s.name) == name.lower())
                    .order_by(desc(s.id))
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        ips = (
            (
                await conn.execute(
                    select(
                        s.ip,
                        s.ip_trusted,
                        func.count().label("sessions"),
                        func.min(s.login_at).label("first_seen"),
                        func.max(s.login_at).label("last_seen"),
                    )
                    .where(and_(func.lower(s.name) == name.lower(), s.ip.is_not(None)))
                    .group_by(s.ip, s.ip_trusted)
                    .order_by(desc("last_seen"))
                )
            )
            .mappings()
            .all()
        )
    return [_session_json(r) for r in rows], [
        {
            "ip": r["ip"],
            "trusted": bool(r["ip_trusted"]),
            "sessions": r["sessions"],
            "first_seen": _iso(r["first_seen"]),
            "last_seen": _iso(r["last_seen"]),
        }
        for r in ips
    ]


async def online_roster(engine: AsyncEngine) -> tuple[list[str], list[str]]:
    """当前在线（未关闭的会话）→ (真人, 假人)，按登录先后。假人 = carpet 等以 [local] 方式登录。"""
    s = db.player_sessions.c
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(s.name, s.fake).where(s.logout_at.is_(None)).order_by(s.login_at)
            )
        ).all()
    real: list[str] = []
    fake: list[str] = []
    for name, is_fake in rows:
        bucket = fake if is_fake else real
        if name not in bucket:
            bucket.append(name)
    return real, fake


async def recent_sessions(
    engine: AsyncEngine, limit: int = 100, online: bool | None = None
) -> list[dict[str, Any]]:
    s = db.player_sessions.c
    q = select(db.player_sessions).where(s.fake.is_(False)).order_by(desc(s.id)).limit(limit)
    if online is True:
        q = q.where(s.logout_at.is_(None))
    elif online is False:
        q = q.where(s.logout_at.is_not(None))
    async with engine.connect() as conn:
        return [_session_json(r) for r in (await conn.execute(q)).mappings()]


async def ip_info(engine: AsyncEngine, ip: str, banned: set[str]) -> dict[str, Any]:
    s = db.player_sessions.c
    f = db.frps_conns.c
    async with engine.connect() as conn:
        accts = (
            await conn.execute(
                select(s.name, func.count().label("n"), func.min(s.login_at), func.max(s.login_at))
                .where(s.ip == ip)
                .group_by(s.name)
                .order_by(desc("n"))
            )
        ).all()
        frps = (
            await conn.execute(
                select(f.proxy, func.count(), func.min(f.ts), func.max(f.ts))
                .where(f.ip == ip)
                .group_by(f.proxy)
            )
        ).all()
    firsts = [a[2] for a in accts] + [r[2] for r in frps]
    lasts = [a[3] for a in accts] + [r[3] for r in frps]

    def iso(xs: list[Any], fn: Any) -> str | None:
        xs = [_aware(x) for x in xs if x is not None]
        return fn(xs).isoformat() if xs else None

    return {
        "ip": ip,
        "first_seen": iso(firsts, min),
        "last_seen": iso(lasts, max),
        "sessions": sum(a[1] for a in accts),
        "accounts": [{"name": a[0], "sessions": a[1]} for a in accts],
        "frps": {
            "connections": sum(r[1] for r in frps),
            "by_proxy": {r[0]: r[1] for r in frps},
            "first_seen": iso([r[2] for r in frps], min),
            "last_seen": iso([r[3] for r in frps], max),
        },
        "banned": ip in banned,
    }


def utcnow() -> datetime:
    return datetime.now(UTC)
