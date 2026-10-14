"""网络页（M4）：frps 链路状态、连接统计、IP 画像、封禁管理。

流量路径：玩家 → 云主机 frps → frp 隧道 → 家里 frpc（PROXY protocol）→ MC。
数据来源：
- frps_conns：边缘采集写入的每条外部连接（proxy='minecraft' / 'ssh'）。
- player_sessions：MC 自己记录的登录。
- game_dir/banned-ips.json：MC 的封禁名单（RCON ban-ip/pardon-ip 维护）。

RCON 执行的命令不会写进 latest.log，所以封禁/解封后由本模块主动发事件
（ip_banned / ip_unbanned），让规则引擎与前端能感知。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db, slp

log = logging.getLogger(__name__)

# 没配置边缘采集时展示的默认端口表；配置了则用 edge.public_ports。
PORT_TABLE: list[dict[str, Any]] = [
    {"port": 25565, "proxy": "minecraft", "desc": "Minecraft"},
    {"port": 7000, "proxy": "frp", "desc": "frp 控制连接"},
]

# 时间窗口 → (时间跨度, 时间序列按天还是按小时)。
_WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
_SCANNER_MIN_CONNS = 3
_SCANNER_BAN_LIMIT = 50
_RECENT_DEFAULT = 200

# 禁止封禁的代理源地址（frpc 把玩家真实 IP 透传，但自身回环来源用这个）。


class NetworkError(ValueError):
    """参数或状态错误（API 映射为 4xx）。"""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def window_delta(window: str) -> timedelta:
    try:
        return _WINDOWS[window]
    except KeyError:
        raise NetworkError(422, f"未知的时间窗口：{window}") from None


def _bucket_mode(window: str) -> str:
    """24h 及以内按小时，7d / 30d 按天。"""
    return "hour" if window in ("1h", "24h") else "day"


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# ---- 链路概览 --------------------------------------------------------------


async def _frpc_active() -> str:
    """本机 frpc 单元是否 active。失败返回 unknown。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "is-active",
            "frpc",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except (OSError, TimeoutError):
        return "unknown"
    text = out.decode(errors="replace").strip()
    # is-active 对未知/失败单元退出码非 0，但 stdout 仍是 "inactive"/"failed"/"unknown"。
    return text or "unknown"


async def _mc_slp(port: int) -> dict[str, Any]:
    """本地 SLP 自检：查询 127.0.0.1:<port>，返回在线人数与延迟。"""
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        r = await slp.ping("127.0.0.1", port, timeout=3.0)
    except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as e:
        return {"ok": False, "error": type(e).__name__}
    latency_ms = round((loop.time() - start) * 1000, 1)
    return {
        "ok": True,
        "online": r.online,
        "max": r.max,
        "latency_ms": latency_ms,
        "version": r.version,
    }


async def overview(s: Any) -> dict[str, Any]:
    """链路状态 + 端口表。"""
    edge = (
        s.edge.status()
        if s.edge is not None
        else {
            "enabled": False,
            "connected": False,
            "last_event_ts": None,
            "error": None,
        }
    )
    frpc = await _frpc_active()
    mc = await _mc_slp(s.server.cfg.port)
    return {
        "edge": edge,
        "frpc": {"status": frpc},
        "mc": mc,
        "frps_host": s.edge.host if s.edge is not None else None,
        "ports": (
            [p.model_dump() for p in s.edge.public_ports] if s.edge is not None else PORT_TABLE
        ),
    }


# ---- 连接统计 --------------------------------------------------------------


def _local_offset_minutes() -> int:
    """服务器本地时区相对 UTC 的偏移（分钟）。mc-server 是 Asia/Shanghai -> 480。"""
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off else 0


async def stats(
    engine: AsyncEngine, window: str, *, tz_offset_min: int | None = None
) -> dict[str, Any]:
    delta = window_delta(window)
    since = datetime.now(UTC) - delta
    f = db.frps_conns.c
    # 库里 ts 存的是 UTC；分桶前先换到本地时区，否则横轴是 UTC、按天的桶也会在 08:00 切日。
    offset = _local_offset_minutes() if tz_offset_min is None else tz_offset_min
    shift = f"{offset:+d} minutes"

    async with engine.connect() as conn:
        totals = (
            await conn.execute(
                select(
                    f.proxy,
                    func.count().label("conns"),
                    func.count(func.distinct(f.ip)).label("ips"),
                )
                .where(f.ts >= since)
                .group_by(f.proxy)
            )
        ).all()

        # 时间序列：按小时或按天分桶，SQLite strftime 聚合，不读全表。
        if _bucket_mode(window) == "hour":
            fmt = "%Y-%m-%dT%H:00"
        else:
            fmt = "%Y-%m-%d"
        bucket = func.strftime(fmt, f.ts, shift).label("bucket")
        series_rows = (
            await conn.execute(
                select(bucket, f.proxy, func.count().label("n"))
                .where(f.ts >= since)
                .group_by(bucket, f.proxy)
                .order_by(bucket)
            )
        ).all()

    by_proxy = {r.proxy: {"connections": int(r.conns), "distinct_ips": int(r.ips)} for r in totals}
    # 时间序列整理成 {bucket: {proxy: n}}，再拉平成有序列表。
    buckets: dict[str, dict[str, int]] = {}
    proxies: set[str] = set()
    for r in series_rows:
        buckets.setdefault(r.bucket, {})[r.proxy] = int(r.n)
        proxies.add(r.proxy)
    series = [{"bucket": b, "counts": buckets[b]} for b in sorted(buckets)]
    return {
        "window": window,
        "bucket": _bucket_mode(window),
        "tz_offset_min": offset,
        "by_proxy": by_proxy,
        "proxies": sorted(proxies | set(by_proxy)),
        "series": series,
    }


# ---- IP 画像 ---------------------------------------------------------------


def _tags(
    *,
    proxies: set[str],
    conns: int,
    has_player: bool,
    banned: bool,
) -> list[str]:
    tags: list[str] = []
    if has_player:
        tags.append("player")
    # scanner：只连过 minecraft、从没进过服，且连接 ≥3 次。
    if proxies == {"minecraft"} and not has_player and conns >= _SCANNER_MIN_CONNS:
        tags.append("scanner")
    if "ssh" in proxies:
        tags.append("ssh")
    if banned:
        tags.append("banned")
    return tags


async def _ip_rows(engine: AsyncEngine, since: datetime) -> dict[str, dict[str, Any]]:
    """按 IP 聚合 frps_conns + player_sessions。返回 {ip: 聚合字段}。"""
    f = db.frps_conns.c
    p = db.player_sessions.c
    out: dict[str, dict[str, Any]] = {}
    async with engine.connect() as conn:
        frps = (
            await conn.execute(
                select(
                    f.ip,
                    f.proxy,
                    func.count().label("n"),
                    func.min(f.ts).label("first"),
                    func.max(f.ts).label("last"),
                )
                .where(f.ts >= since)
                .group_by(f.ip, f.proxy)
            )
        ).all()
        players = (
            await conn.execute(
                select(
                    p.ip,
                    p.name,
                    func.count().label("n"),
                    func.max(p.login_at).label("last"),
                )
                .where(p.ip.is_not(None), p.fake.is_(False))
                .group_by(p.ip, p.name)
            )
        ).all()

    for r in frps:
        e = out.setdefault(
            r.ip,
            {"conns": 0, "proxies": set(), "first": None, "last": None, "players": {}},
        )
        e["conns"] += int(r.n)
        e["proxies"].add(r.proxy)
        first, last = _aware(r.first), _aware(r.last)
        e["first"] = first if e["first"] is None else min(e["first"], first)
        e["last"] = last if e["last"] is None else max(e["last"], last)
    for r in players:
        e = out.setdefault(
            r.ip,
            {"conns": 0, "proxies": set(), "first": None, "last": None, "players": {}},
        )
        prev = e["players"].get(r.name)
        plast = _aware(r.last) if r.last is not None else None
        if prev is None or (plast is not None and (prev[1] is None or plast > prev[1])):
            e["players"][r.name] = (int(r.n), plast)
        else:
            e["players"][r.name] = (prev[0] + int(r.n), prev[1])
    return out


async def ip_list(
    engine: AsyncEngine,
    banned: set[str],
    *,
    window: str = "24h",
    q: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    delta = window_delta(window)
    since = datetime.now(UTC) - delta
    rows = await _ip_rows(engine, since)

    items: list[dict[str, Any]] = []
    for ip, e in rows.items():
        proxies: set[str] = e["proxies"]
        players = e["players"]
        has_player = bool(players)
        is_banned = ip in banned
        tags = _tags(proxies=proxies, conns=e["conns"], has_player=has_player, banned=is_banned)
        player_last = [_aware(v[1]) for v in players.values() if v[1] is not None]
        items.append(
            {
                "ip": ip,
                "conns": e["conns"],
                "first_seen": _iso(e["first"]),
                "last_seen": _iso(e["last"]),
                "proxies": sorted(proxies),
                "banned": is_banned,
                "tags": tags,
                "players": sorted(players),
                "player_last_login": _iso(max(player_last)) if player_last else None,
            }
        )

    if q:
        needle = q.lower()
        items = [
            it
            for it in items
            if needle in it["ip"].lower() or any(needle in n.lower() for n in it["players"])
        ]
    if tag:
        items = [it for it in items if tag in it["tags"]]

    # 连接数倒序，再按最近出现倒序。
    items.sort(key=lambda it: (it["conns"], it["last_seen"] or ""), reverse=True)
    total = len(items)
    page = items[offset : offset + limit]
    return {"items": page, "total": total, "window": window, "limit": limit, "offset": offset}


# ---- 最近连接流水 ----------------------------------------------------------


async def recent(
    engine: AsyncEngine, banned: set[str], limit: int = _RECENT_DEFAULT
) -> dict[str, Any]:
    f = db.frps_conns.c
    p = db.player_sessions.c
    limit = max(1, min(limit, 1000))
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(f.id, f.ts, f.proxy, f.ip, f.port).order_by(f.id.desc()).limit(limit)
            )
        ).all()
        ips = {r.ip for r in rows}
        player_map: dict[str, list[str]] = {}
        proxy_map: dict[str, set[str]] = {}
        conns_map: dict[str, int] = {}
        if ips:
            prows = (
                await conn.execute(
                    select(p.ip, p.name)
                    .where(p.ip.in_(ips), p.fake.is_(False))
                    .group_by(p.ip, p.name)
                )
            ).all()
            for pr in prows:
                player_map.setdefault(pr.ip, []).append(pr.name)
            # 为标签判定取每个 IP 的 proxy 集合与总连接数。
            agg = (
                await conn.execute(
                    select(f.ip, f.proxy, func.count().label("n"))
                    .where(f.ip.in_(ips))
                    .group_by(f.ip, f.proxy)
                )
            ).all()
            for ar in agg:
                proxy_map.setdefault(ar.ip, set()).add(ar.proxy)
                conns_map[ar.ip] = conns_map.get(ar.ip, 0) + int(ar.n)

    items = []
    for r in rows:
        players = sorted(player_map.get(r.ip, []))
        tags = _tags(
            proxies=proxy_map.get(r.ip, {r.proxy}),
            conns=conns_map.get(r.ip, 1),
            has_player=bool(players),
            banned=r.ip in banned,
        )
        items.append(
            {
                "ts": _iso(r.ts),
                "proxy": r.proxy,
                "ip": r.ip,
                "port": r.port,
                "tags": tags,
                "players": players,
            }
        )
    return {"items": items}


# ---- 封禁管理 --------------------------------------------------------------


def list_bans(game_dir: Path) -> list[dict[str, Any]]:
    """解析 game_dir/banned-ips.json。"""
    try:
        data = json.loads((game_dir / "banned-ips.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    out = []
    for e in data:
        if not isinstance(e, dict) or "ip" not in e:
            continue
        out.append(
            {
                "ip": e["ip"],
                "reason": e.get("reason"),
                "created": e.get("created"),
                "source": e.get("source"),
                "expires": e.get("expires"),
            }
        )
    return out


def validate_ban_ip(ip: str, protected: Sequence[str] = ()) -> str:
    """校验要封禁的 IP：非法 → 422；回环/私网/链路本地/代理源 → 422。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        raise NetworkError(422, f"不是合法的 IP 地址：{ip}") from None
    if str(addr) in protected:
        raise NetworkError(422, f"{addr} 是代理源地址，封禁它会误伤所有玩家")
    if addr.is_loopback:
        raise NetworkError(422, "拒绝封禁回环地址")
    if addr.is_private:
        raise NetworkError(422, "拒绝封禁私网地址")
    if addr.is_link_local:
        raise NetworkError(422, "拒绝封禁链路本地地址")
    return str(addr)


def sanitize_ban_reason(reason: str | None) -> str | None:
    """封禁理由只保留可见字符：去掉控制字符（含换行），再压缩空白，避免拼进 RCON 命令时作怪。"""
    if reason is None:
        return None
    # 控制字符（含换行/制表）换成空格而不是直接删，避免把两个词粘在一起；再压缩多余空白
    cleaned = "".join(ch if ch.isprintable() else " " for ch in reason)
    cleaned = " ".join(cleaned.split())
    return cleaned or None


async def scanners(engine: AsyncEngine, window: str = "24h") -> dict[str, Any]:
    """扫描器候选预览：只连过 minecraft、从没进过服、连接 ≥3 次的 IP。"""
    delta = window_delta(window)
    since = datetime.now(UTC) - delta
    rows = await _ip_rows(engine, since)
    out = []
    for ip, e in rows.items():
        if e["proxies"] == {"minecraft"} and not e["players"] and e["conns"] >= _SCANNER_MIN_CONNS:
            out.append(
                {
                    "ip": ip,
                    "conns": e["conns"],
                    "first_seen": _iso(e["first"]),
                    "last_seen": _iso(e["last"]),
                }
            )
    out.sort(key=lambda it: it["conns"], reverse=True)
    return {"window": window, "count": len(out), "items": out}


async def ssh_sources(engine: AsyncEngine, hours: int = 24) -> dict[str, Any]:
    """最近 N 小时连过 ssh proxy 的 IP（醒目提示用）。"""
    since = datetime.now(UTC) - timedelta(hours=hours)
    f = db.frps_conns.c
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    f.ip,
                    func.count().label("n"),
                    func.min(f.ts).label("first"),
                    func.max(f.ts).label("last"),
                )
                .where(f.ts >= since, f.proxy == "ssh")
                .group_by(f.ip)
                .order_by(func.max(f.ts).desc())
            )
        ).all()
    return {
        "hours": hours,
        "items": [
            {
                "ip": r.ip,
                "conns": int(r.n),
                "first_seen": _iso(_aware(r.first)),
                "last_seen": _iso(_aware(r.last)),
            }
            for r in rows
        ],
    }
