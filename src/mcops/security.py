"""安全规则引擎。首批规则全部来自 2026-10-04 冒用破坏事件的经验（docs/01-design.md §3.5）。

- 事件驱动：handle(ev, change) 由事件总线消费者调用；tick(now) 处理需要延时判断的规则。
- 去重：同一 (rule, subject) 在冷却窗口内只累加 count，不重复通知。
- 回填（historical=True）产生的告警直接标记已确认，只用于复盘，不发通知。
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from sqlalchemy import and_, desc, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.events import Event
from mcops.players import SessionChange, ip_prefix, ip_trusted

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    name: str
    description: str
    severity: str


RULES: dict[str, Rule] = {
    r.id: r
    for r in [
        Rule(
            "night_login", "凌晨登录", "01:00–06:00 有真实玩家登录（10-04 作案在 02:47）", "warning"
        ),
        Rule(
            "new_ip",
            "已知账号出现新 IP 段",
            "账号有 ≥3 次可信 IP 记录后，从未见过的 /24 网段登录",
            "warning",
        ),
        Rule("new_account", "新账号首次进服", "从未出现过的账号（含 EasyAuth 注册）", "warning"),
        Rule(
            "ip_multi_account",
            "一个 IP 多个账号",
            "24 小时内同一真实 IP 登录 ≥3 个不同账号",
            "critical",
        ),
        Rule(
            "auth_failure_burst",
            "疑似爆破密码",
            "30 分钟内同一账号 ≥3 次未通过 EasyAuth 认证即离开或密码错误",
            "critical",
        ),
        Rule(
            "anticheat_kick",
            "因飞行等被踢（需复核）",
            "原版检测踢出玩家（悬空过久等），卡顿时常误判；凌晨或 30 分钟内反复被踢时升级为严重",
            "warning",
        ),
        Rule(
            "anticheat_burst",
            "外挂特征：频繁移动异常",
            "鞘翅飞行时 moved too quickly 频率远超正常（默认 5 分钟 ≥15 次；正常烟花飞行最多约 8 次）；"
            "无法确认是否在鞘翅飞行时按 2 分钟 ≥5 次",
            "warning",
        ),
        Rule(
            "speed_no_elytra",
            "外挂特征：非鞘翅超速",
            "出现 moved too quickly 时玩家并未在用鞘翅飞行（加速挂特征；三叉戟激流、被炸飞也可能触发）；"
            "向下且水平几乎不动的视为下落，不告警；凌晨或 2 分钟内 ≥3 次升级为严重",
            "warning",
        ),
        Rule(
            "fingerprint_mismatch",
            "客户端指纹不符",
            "一贯使用 carpet 客户端的账号，本次登录 20 秒内没有 carpet 握手（10-04 证明冒用的关键证据）",
            "critical",
        ),
        Rule(
            "impersonation_attempt",
            "冒用玩家名尝试进服",
            "进服被拒的连接用了已知玩家的名字，且来自已封禁 IP 或该账号从未用过的网段"
            "（扫描器会从服务器列表拿在线玩家名来试）；来自已封禁 IP 时为严重",
            "warning",
        ),
        Rule(
            "frps_ssh_burst",
            "SSH 隧道连接激增",
            "10 分钟内同一 IP 经 frps 连接 SSH ≥20 次",
            "warning",
        ),
        Rule(
            "flight_suspect",
            "疑似飞行（只告警）",
            "生存模式下连续悬空且不下落；服务端允许飞行不踢人，由面板告警人工核实",
            "warning",
        ),
        Rule(
            "backup_offsite_failed",
            "异地备份失败",
            "把增量快照复制到异地仓库（对象存储 / NAS）失败；本地快照不受影响",
            "warning",
        ),
        Rule("server_crash", "服务器意外停止", "进程意外退出或启动失败，已自动重启", "warning"),
        Rule(
            "server_crash_loop",
            "服务器反复崩溃",
            "窗口期内崩溃达到阈值，已停止自动重启，需要人工处理",
            "critical",
        ),
        Rule(
            "unregistered_whitelisted",
            "白名单账号未注册",
            "白名单内但 EasyAuth 未设置密码，任何人可抢注冒用",
            "critical",
        ),
    ]
}

COOLDOWN = timedelta(hours=1)


@dataclass
class SecurityConfig:
    night_start_hour: int = 1
    night_end_hour: int = 6
    ip_allowlist: set[str] = field(default_factory=set)
    disabled_rules: set[str] = field(default_factory=set)
    severity_overrides: dict[str, str] = field(default_factory=dict)
    """面板「设置 → 通知」里的级别覆盖。规则自己的升级（如凌晨 → critical）仍然优先。"""
    elytra_burst_count: int = 15
    elytra_burst_window_minutes: int = 5


Notifier = Callable[[dict[str, Any]], Awaitable[None]]
ElytraProbe = Callable[[str], Awaitable[bool | None]]
"""玩家此刻是否在鞘翅飞行；None = 无法判断。"""


def _move_delta(detail: str) -> tuple[float, float, float] | None:
    """'moved too quickly! dx,dy,dz' 的 detail → (dx, dy, dz)。"""
    try:
        dx, dy, dz = (float(v) for v in detail.strip(" !").split(","))
    except ValueError:
        return None
    return dx, dy, dz


def _move_distance(detail: str) -> float | None:
    """'moved too quickly! dx,dy,dz' 的 detail → 位移长度（格）。"""
    delta = _move_delta(detail)
    if delta is None:
        return None
    dx, dy, dz = delta
    return float((dx * dx + dy * dy + dz * dz) ** 0.5)


FALL_MAX_HORIZONTAL = 4.0
"""向下位移时，水平分量不超过这么多格就视为下落（疾跑跳跃每 tick 约 0.6 格）。"""


def is_fall(detail: str) -> bool:
    """超速主要来自下落：向下移动、水平几乎不动。

    高处跳下 / 落进虚空 / 卡顿后补发位置时，原版把整段下落算成一次超速，属于误报；
    加速挂改的是水平速度，不会表现为这种形态。
    """
    delta = _move_delta(detail)
    if delta is None:
        return False
    dx, dy, dz = delta
    return dy < 0 and (dx * dx + dz * dz) ** 0.5 <= FALL_MAX_HORIZONTAL


class SecurityEngine:
    def __init__(
        self,
        engine: AsyncEngine,
        local_tz: tzinfo,
        cfg: SecurityConfig | None = None,
        notifier: Notifier | None = None,
        historical: bool = False,
        on_alert: Callable[[dict[str, Any]], None] | None = None,
        elytra_probe: ElytraProbe | None = None,
        banned_ips: Callable[[], set[str]] | None = None,
    ) -> None:
        self.engine = engine
        self.tz = local_tz
        self.cfg = cfg or SecurityConfig()
        self.notifier = notifier
        self.historical = historical
        self.on_alert = on_alert
        self.elytra_probe = elytra_probe
        self.banned_ips = banned_ips
        self._windows: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)
        self._pending_fp: dict[int, tuple[datetime, dict[str, Any]]] = {}

    # ---- 告警落库与去重 ----------------------------------------------------

    async def raise_alert(
        self,
        rule_id: str,
        ts: datetime,
        title: str,
        detail: str,
        subject: str | None,
        data: dict[str, Any],
        severity: str | None = None,
    ) -> dict[str, Any] | None:
        if rule_id in self.cfg.disabled_rules:
            return None
        rule = RULES[rule_id]
        sev = severity or self.cfg.severity_overrides.get(rule_id) or rule.severity
        a = db.alerts.c
        async with self.engine.begin() as conn:
            existing = (
                (
                    await conn.execute(
                        select(db.alerts)
                        .where(
                            and_(
                                a.rule == rule_id,
                                a.subject.is_(None) if subject is None else a.subject == subject,
                                a.historical.is_(self.historical),
                                a.last_ts >= ts - COOLDOWN,
                            )
                        )
                        .order_by(desc(a.id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
            # 冷却窗口内同一 (rule, subject) 只累加；已确认的告警只要问题仍在持续，也不重复打扰
            if existing is not None:
                await conn.execute(
                    update(db.alerts)
                    .where(a.id == existing["id"])
                    .values(
                        last_ts=max(ts, _aware(existing["last_ts"])), count=existing["count"] + 1
                    )
                )
                return None
            row = {
                "first_ts": ts,
                "last_ts": ts,
                "rule": rule_id,
                "severity": sev,
                "title": title,
                "detail": detail,
                "subject": subject,
                "data": data,
                "status": "acked" if self.historical else "open",
                "acked_at": ts if self.historical else None,
                "count": 1,
                "historical": self.historical,
            }
            res = await conn.execute(insert(db.alerts).values(**row))
            row["id"] = int(res.inserted_primary_key[0])  # type: ignore[index]
        alert = alert_json(row)
        log.warning("告警 [%s] %s：%s", sev, title, detail)
        if not self.historical:
            if self.on_alert:
                self.on_alert(alert)
            if self.notifier:
                try:
                    await self.notifier(alert)
                except Exception:
                    log.exception("告警通知发送失败")
        return alert

    def _window(self, key: tuple[str, str], ts: datetime, span: timedelta) -> int:
        q = self._windows[key]
        q.append(ts)
        while q and ts - q[0] > span:
            q.popleft()
        return len(q)

    def _local(self, ts: datetime) -> datetime:
        return ts.astimezone(self.tz)

    # ---- 事件入口 ----------------------------------------------------------

    async def handle(self, ev: Event, change: SessionChange | None) -> None:
        await self.tick(ev.ts)
        d = ev.data
        if ev.type == "player_login" and change is not None and not d.get("fake"):
            await self._on_login(ev, change.session)
        elif ev.type == "auth_register":
            await self.raise_alert(
                "new_account",
                ev.ts,
                f"新账号注册：{d['name']}",
                "EasyAuth 记录到新账号注册",
                d["name"],
                dict(d),
            )
        elif ev.type == "auth_failure":
            await self._auth_failed(ev.ts, d["name"], "密码错误")
        elif ev.type == "player_leave" and change is not None:
            s = change.session
            if (
                not s["fake"]
                and s.get("authenticated") is False
                and s.get("logout_at") is not None
                and (s["logout_at"] - _aware(s["login_at"])) <= timedelta(seconds=90)
            ):
                await self._auth_failed(ev.ts, s["name"], "未完成登录认证即离开")
            self._pending_fp.pop(s["id"], None)
        elif ev.type == "anticheat":
            await self._on_anticheat(ev)
        elif ev.type == "login_rejected":
            await self._on_rejected(ev)
        elif ev.type == "frps_conn" and d.get("proxy") == "ssh":
            ip = d["ip"]
            if ip in self.cfg.ip_allowlist:
                return
            n = self._window(("frps_ssh", ip), ev.ts, timedelta(minutes=10))
            if n == 20:
                await self.raise_alert(
                    "frps_ssh_burst",
                    ev.ts,
                    f"SSH 隧道被 {ip} 高频连接",
                    "10 分钟内 ≥20 次 SSH 连接（mc-server 只认公钥，当前为观测级别）",
                    ip,
                    {"ip": ip, "count": n},
                )

    async def _on_login(self, ev: Event, sess: dict[str, Any]) -> None:
        name, ip = sess["name"], sess.get("ip")
        trusted = ip_trusted(ip)
        hist = await self._history(name, exclude_id=sess["id"])
        local = self._local(ev.ts)
        night = self.cfg.night_start_hour <= local.hour < self.cfg.night_end_hour

        if not hist["total"]:
            await self.raise_alert(
                "new_account",
                ev.ts,
                f"新账号首次进服：{name}",
                f"来源 {ip or '未知'}",
                name,
                {"name": name, "ip": ip},
            )

        new_prefix = False
        if trusted and ip not in self.cfg.ip_allowlist and hist["trusted"] >= 3:
            assert ip is not None
            new_prefix = ip_prefix(ip) not in hist["prefixes"]
            if new_prefix:
                await self.raise_alert(
                    "new_ip",
                    ev.ts,
                    f"{name} 从新网段登录：{ip}",
                    f"该账号此前出现过的网段：{', '.join(sorted(hist['prefixes'])[:5])}",
                    name,
                    {"name": name, "ip": ip, "known_prefixes": sorted(hist["prefixes"])},
                )

        if night:
            await self.raise_alert(
                "night_login",
                ev.ts,
                f"凌晨登录：{name}（{local:%H:%M}）",
                f"来源 {ip or '未知'}" + ("，且是新网段" if new_prefix else ""),
                name,
                {"name": name, "ip": ip, "local_time": local.isoformat()},
                severity="critical" if new_prefix else None,
            )

        if trusted and ip not in self.cfg.ip_allowlist:
            assert ip is not None
            names = await self._names_on_ip(ip, ev.ts - timedelta(hours=24))
            if len(names) >= 3:
                await self.raise_alert(
                    "ip_multi_account",
                    ev.ts,
                    f"IP {ip} 24 小时内登录了 {len(names)} 个账号",
                    "、".join(sorted(names)),
                    ip,
                    {"ip": ip, "accounts": sorted(names)},
                )

        # carpet 一贯用户（最近 5 次已知记录全是 carpet）：登录后 20 秒内应出现握手。
        # 玩家换客户端很常见，单独出现只算警告；叠加凌晨/新网段才升级（生产回放校准）。
        if hist["carpet_recent5"] == 5:
            sess = {**sess, "_escalate": night or new_prefix}
            self._pending_fp[sess["id"]] = (ev.ts + timedelta(seconds=20), sess)

    async def tick(self, now: datetime) -> None:
        due = [sid for sid, (t, _) in self._pending_fp.items() if t <= now]
        for sid in due:
            _, sess = self._pending_fp.pop(sid)
            async with self.engine.connect() as conn:
                cur = (
                    await conn.execute(
                        select(db.player_sessions.c.carpet_client).where(
                            db.player_sessions.c.id == sid
                        )
                    )
                ).scalar()
            if cur is not True:
                await self.raise_alert(
                    "fingerprint_mismatch",
                    now,
                    f"{sess['name']} 客户端指纹不符，疑似冒用",
                    "该账号以往登录都使用 carpet 客户端，本次没有握手。"
                    f"来源 {sess.get('ip') or '未知'}。请核实是否本人。",
                    sess["name"],
                    {"name": sess["name"], "ip": sess.get("ip"), "session_id": sid},
                )

    async def _auth_failed(self, ts: datetime, name: str, why: str) -> None:
        n = self._window(("auth_fail", name.lower()), ts, timedelta(minutes=30))
        if n >= 3:
            await self.raise_alert(
                "auth_failure_burst",
                ts,
                f"{name} 疑似被爆破密码",
                f"30 分钟内 {n} 次认证失败（最近一次：{why}）",
                name,
                {"name": name, "count": n},
            )

    async def _on_anticheat(self, ev: Event) -> None:
        d = ev.data
        if d.get("kind") == "kicked":
            # 原版 "floating too long" 在网络卡顿/区块未加载时经常误判，单次只做 warning；
            # 凌晨或 30 分钟内同一人反复被踢才升级（10-04 攻击者凌晨开飞行被踢后继续破坏）
            n = self._window(("kick", d["name"].lower()), ev.ts, timedelta(minutes=30))
            local = ev.ts.astimezone(self.tz)
            night = self.cfg.night_start_hour <= local.hour < self.cfg.night_end_hour
            why = [w for w, hit in (("凌晨", night), (f"30 分钟内第 {n} 次", n >= 2)) if hit]
            await self.raise_alert(
                "anticheat_kick",
                ev.ts,
                f"{d['name']} 因飞行等被踢：{d.get('detail', '')}",
                (
                    "已升级：" + "、".join(why) + "。"
                    if why
                    else "单次被踢多为卡顿误判，可问问玩家。"
                )
                + "10-04 攻击者正是开飞行被踢后继续破坏。",
                d["name"],
                {**d, "count_30m": n},
                severity="critical" if why else None,
            )
        elif d.get("kind") in ("moved too quickly", "moved wrongly"):
            await self._on_fast_move(ev)

    async def _on_fast_move(self, ev: Event) -> None:
        """原版 moved too quickly：鞘翅 + 烟花 + 卡顿时大量误报（实测正常飞行每分钟约 1 次）。

        - 实时事件先经 RCON 查玩家此刻是否在鞘翅飞行（FallFlying 由服务端维护）；
        - 不在飞 → speed_no_elytra（每次都记，加速挂特征）；
        - 在飞 → 只有频率远超正常飞行才告警；
        - 无法判断（回填 / RCON 不可用 / moved wrongly）→ 沿用 2 分钟 ≥5 次。

        向下、水平几乎不动的超速是下落（is_fall），整条忽略，不计数也不查 RCON。
        """
        d = ev.data
        name = d["name"]
        if d.get("kind") == "moved too quickly" and is_fall(d.get("detail", "")):
            log.debug("%s moved too quickly 为下落（%s），忽略", name, d.get("detail"))
            return
        dist = _move_distance(d.get("detail", ""))
        data = {**d, "distance": None if dist is None else round(dist, 1)}
        flying: bool | None = None
        if (
            d.get("kind") == "moved too quickly"
            and self.elytra_probe is not None
            and not self.historical
        ):
            flying = await self.elytra_probe(name)
        data["elytra"] = flying
        span = f"，单次位移约 {dist:.0f} 格" if dist is not None else ""

        if flying is False:
            n = self._window(("move_ground", name), ev.ts, timedelta(minutes=2))
            local = ev.ts.astimezone(self.tz)
            night = self.cfg.night_start_hour <= local.hour < self.cfg.night_end_hour
            why = [w for w, hit in (("凌晨", night), (f"2 分钟内第 {n} 次", n >= 3)) if hit]
            await self.raise_alert(
                "speed_no_elytra",
                ev.ts,
                f"{name} 非鞘翅超速移动",
                f"moved too quickly 时玩家没有在用鞘翅飞行{span}。"
                "可能是加速挂；三叉戟激流、被炸飞、刚落地也可能触发，请看回放或问玩家。"
                + ("已升级：" + "、".join(why) + "。" if why else ""),
                name,
                {**data, "count_2m": n},
                severity="critical" if why else None,
            )
        elif flying is True:
            win = self.cfg.elytra_burst_window_minutes
            n = self._window(("move_elytra", name), ev.ts, timedelta(minutes=win))
            if n == self.cfg.elytra_burst_count:
                await self.raise_alert(
                    "anticheat_burst",
                    ev.ts,
                    f"{name} 鞘翅飞行时移动异常过于频繁",
                    f"{win} 分钟内 {n} 次 moved too quickly（鞘翅飞行中{span}）。"
                    "正常烟花飞行加卡顿约每分钟 1-2 次，远高于此可能是鞘翅加速挂，也可能是服务器严重卡顿。",
                    name,
                    {**data, "count": n, "window_minutes": win},
                )
        else:
            n = self._window(("move", name), ev.ts, timedelta(minutes=2))
            if n == 5:
                await self.raise_alert(
                    "anticheat_burst",
                    ev.ts,
                    f"{name} 频繁移动异常",
                    f"2 分钟内 ≥5 次 {d.get('kind')}（无法确认是否在鞘翅飞行；"
                    "可能是外挂，也可能是网络卡顿）",
                    name,
                    {**data, "count": n},
                )

    async def check_unregistered(
        self, whitelist: list[str], registered: set[str] | None, now: datetime
    ) -> None:
        if registered is None:
            return
        for name in whitelist:
            if name.lower() not in registered:
                await self.raise_alert(
                    "unregistered_whitelisted",
                    now,
                    f"白名单账号 {name} 未在 EasyAuth 注册",
                    "离线模式下任何人都能用这个名字进服并抢先 /register。"
                    "请为该账号预设密码，或先移出白名单。",
                    name,
                    {"name": name},
                )

    async def _on_rejected(self, ev: Event) -> None:
        """进服前被拒的连接用了已知玩家名：10-13 02:31 扫描器 93.184.216.198 冒用在线玩家名。

        本人掉线重连（同网段 "already online"）、服务器满员等不算；名字没进过服的（扫描器
        平时用的 FishNet 之类）也不算，白名单已经挡住了。
        """
        d = ev.data
        name, ip = d["name"], d["ip"]
        if ip in self.cfg.ip_allowlist:
            return
        hist = await self._history(name, exclude_id=-1)
        if not hist["total"]:
            return
        banned = False
        if self.banned_ips is not None:
            try:
                banned = ip in self.banned_ips()
            except Exception:
                log.exception("读取 IP 封禁列表失败")
        if not banned and (not ip_trusted(ip) or ip_prefix(ip) in hist["prefixes"]):
            return
        why = "已封禁的 IP" if banned else "该账号从未用过的网段"
        await self.raise_alert(
            "impersonation_attempt",
            ev.ts,
            f"{ip} 冒用 {name} 尝试进服",
            f"来源是{why}，被拒原因：{d.get('reason', '')}。"
            "扫描器会从服务器列表拿在线玩家名来试；本人的话请问一下是否换了网络。",
            name,
            {"name": name, "ip": ip, "reason": d.get("reason"), "banned_ip": banned},
            severity="critical" if banned else None,
        )

    # ---- 历史查询 ----------------------------------------------------------

    async def _history(self, name: str, exclude_id: int) -> dict[str, Any]:
        s = db.player_sessions.c
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(s.ip, s.ip_trusted, s.carpet_client)
                    .where(
                        and_(
                            func.lower(s.name) == name.lower(),
                            s.id != exclude_id,
                            s.fake.is_(False),
                        )
                    )
                    .order_by(desc(s.id))
                    .limit(200)
                )
            ).all()
        prefixes = {ip_prefix(r[0]) for r in rows if r[1] and r[0]}
        known = [r[2] for r in rows if r[2] is not None][:5]
        return {
            "total": len(rows),
            "trusted": sum(1 for r in rows if r[1]),
            "prefixes": prefixes,
            "carpet_recent5": sum(1 for x in known if x is True) if len(known) == 5 else 0,
        }

    async def _names_on_ip(self, ip: str, since: datetime) -> set[str]:
        s = db.player_sessions.c
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(func.distinct(s.name)).where(
                        and_(s.ip == ip, s.login_at >= since, s.fake.is_(False))
                    )
                )
            ).all()
        return {r[0] for r in rows}


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def alert_json(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "first_ts": _aware(r["first_ts"]).isoformat(),
        "last_ts": _aware(r["last_ts"]).isoformat(),
        "rule": r["rule"],
        "severity": r["severity"],
        "title": r["title"],
        "detail": r["detail"],
        "subject": r["subject"],
        "data": r["data"],
        "status": r["status"],
        "acked_at": _aware(r["acked_at"]).isoformat() if r["acked_at"] else None,
        "count": r["count"],
    }


async def list_alerts(
    engine: AsyncEngine, status: str = "open", limit: int = 100
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    a = db.alerts.c
    q = select(db.alerts).order_by(desc(a.last_ts)).limit(limit)
    if status in ("open", "acked"):
        q = q.where(a.status == status)
    async with engine.connect() as conn:
        rows = (await conn.execute(q)).mappings().all()
        counts: dict[str, int] = dict(
            (
                await conn.execute(
                    select(a.severity, func.count()).where(a.status == "open").group_by(a.severity)
                )
            ).all()
        )
    return [alert_json(r) for r in rows], {
        k: int(counts.get(k, 0)) for k in ("critical", "warning", "info")
    }


async def ack_alerts(
    engine: AsyncEngine, alert_id: int | None = None, severity: str | None = None
) -> int:
    a = db.alerts.c
    cond = a.status == "open"
    if alert_id is not None:
        cond = and_(cond, a.id == alert_id)
    if severity:
        cond = and_(cond, a.severity == severity)
    async with engine.begin() as conn:
        res = await conn.execute(
            update(db.alerts).where(cond).values(status="acked", acked_at=datetime.now(UTC))
        )
    return int(res.rowcount or 0)
