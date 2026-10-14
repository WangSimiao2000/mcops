"""QQ 机器人核心逻辑（与平台无关，便于测试与面板里的指令模拟器复用）。

行为逐条对齐旧 mc-qq-bot/bot.py；不同之处：
- 服务器状态 / 性能 / Mod 列表在进程内直接获取，不再解析 mc.sh 的彩色文本输出；
- 申请、成员、群、交互记录存数据库，不再是散落的 JSON / 日志文件；
- 服务端命令走 ServerChannel（RCON 或过渡期的 mc.sh）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, desc, func, insert, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db, perf, slp
from mcops.bot import texts as T
from mcops.bot.channel import ServerChannel
from mcops.bot.whitelist import WhitelistError, WhitelistFile, parse_whitelist_list, validate_name
from mcops.events import Event
from mcops.slp import ANONYMOUS

log = logging.getLogger(__name__)

Pusher = Callable[[str, set[str]], Awaitable[int]]


@dataclass
class BotSettings:
    display_host: str
    name: str = "Minecraft 服务器 bot"
    query_host: str = "127.0.0.1"
    query_port: int = 25565
    query_timeout: float = 5.0
    join_note: str = ""
    admin_openids: set[str] = field(default_factory=set)
    allowed_groups: set[str] = field(default_factory=set)
    alert_groups: set[str] = field(default_factory=set)
    max_pending_per_user: int = 2
    max_pending_total: int = 20
    monitor_interval: float = 60.0
    fail_threshold: int = 3
    active_alert: bool = True
    planned_actors: set[str] = field(default_factory=lambda: {"mc-restart", "cmd_stop"})
    watchdog_state_file: Path | None = None
    maintenance_signal_file: Path | None = None
    maintenance_cooldown: float = 300.0
    activation_hours: int = 48
    frp_server_port: int = 7000
    """frps 监听端口（frpc 连过去的远端端口）；0 = 状态里不显示隧道延迟。"""


@dataclass
class MCStatus:
    online: bool
    players_online: int = 0
    players_max: int = 0
    player_names: list[str] = field(default_factory=list)
    fake_names: list[str] = field(default_factory=list)
    version: str = ""
    tunnel: perf.TunnelRtt | None = None
    tick: perf.TickStats | None = None
    error: str = ""

    def summary(self) -> str:
        if not self.online:
            return f"🔴 服务器离线\n原因：{self.error or '无法连接'}"
        lines = [
            "🟢 服务器在线",
            f"👥 玩家：{self.players_online}/{self.players_max}{self._split()}",
        ]
        if self.version:
            lines.append(f"📦 版本：{self.version}")
        if self.tick is not None:
            lines.append(_tick_line(self.tick))
        if self.tunnel is not None:
            t = self.tunnel
            low = f"（最低 {t.min_ms:.0f}ms）" if t.min_ms is not None else ""
            lines.append(f"📡 frp 隧道延迟：{t.rtt_ms:.0f}ms{low}")
        return "\n".join(lines)

    def _split(self) -> str:
        if not self.fake_names:
            return ""
        real = max(self.players_online - len(self.fake_names), 0)
        return f"（真人 {real} · 假人 {len(self.fake_names)}）"

    def players_text(self) -> str:
        if not self.online:
            return f"🔴 服务器离线，无法获取玩家列表\n原因：{self.error or '无法连接'}"
        if self.players_online == 0:
            return "🌙 当前无人在线"
        header = f"👥 在线玩家 {self.players_online}/{self.players_max}{self._split()}"
        names = [n for n in self.player_names if n and n != ANONYMOUS]
        if not names and not self.fake_names:
            return header + "\n（服务器未返回玩家名单）"
        lines = [header]
        if names:
            lines.append("🎮 " + "、".join(names[:50]) + (" 等" if len(names) > 50 else ""))
        if self.fake_names:
            fakes = self.fake_names
            lines.append("🤖 假人：" + "、".join(fakes[:50]) + (" 等" if len(fakes) > 50 else ""))
        return "\n".join(lines)


def _tick_line(t: perf.TickStats) -> str:
    if t.frozen:
        return "⚙️ 游戏刻：已冻结（tick freeze）"
    budget = 1000.0 / t.target_rate if t.target_rate > 0 else 50.0
    mark = "🟢" if t.mspt <= budget * 0.8 else "🟡" if t.mspt <= budget else "🔴"
    p95 = f"，P95 {t.p95:.1f}ms" if t.p95 is not None else ""
    return f"{mark} TPS {t.tps:.1f} · MSPT {t.mspt:.1f}ms{p95}"


@dataclass
class Message:
    content: str
    group_openid: str
    actor_openid: str | None


class BotCore:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: BotSettings,
        whitelist: WhitelistFile | None,
        channel: ServerChannel | None,
        game_dir: Path,
        pid_lookup: Callable[[], Awaitable[int | None]],
        roster: Callable[[], Awaitable[tuple[list[str], list[str]]]] | None = None,
        registered: Callable[[], set[str] | None] | None = None,
    ) -> None:
        # EasyAuth 已注册用户名（小写）；None 表示没装 EasyAuth，激活只看是否进过服
        self.registered = registered
        self.engine = engine
        self.s = settings
        self.wl = whitelist
        self.channel = channel
        self.game_dir = game_dir
        self.pid_lookup = pid_lookup
        self.roster = roster
        """在线名单（会话追踪）→ (真人, 假人)。"""
        self.pusher: Pusher | None = None
        # control 模式下由 ServerController 直接告知"本次停机是谁发起的"，取代 .watchdog/state 文件
        self.planned_source: Callable[[], str | None] | None = None
        # 「设为告警群」指令由通知中心实现（NotifyHub.set_alert_group）
        self.alert_group_hook: Callable[[str, bool], Awaitable[bool]] | None = None
        # 群里的指令可能改了申请/白名单：推一条不落库的事件让在线面板刷新
        self.ui_publish: Callable[[Event], None] | None = None
        # 群关闭主动消息时（40034105）的兜底：告警群里有人 @机器人 时，在回复末尾附"有新告警"提示
        self._alert_hinted: dict[str, datetime] = {}
        self._started = datetime.now(UTC)
        # 监控状态机（与旧实现一致）
        self._last_online: bool | None = None
        self._fail_count = 0
        self._in_maintenance = False
        self._maint_mode = False
        self._maint_until = 0.0
        self._maint_saw_down = False
        self._last_maint_announce = 0.0
        self.pushes_today = 0
        self._pushes_day = datetime.now().date()

    # ---- 权限 ----------------------------------------------------------------

    def is_admin(self, msg: Message) -> bool:
        if not msg.actor_openid or not self.s.admin_openids:
            return False
        if self.s.allowed_groups and msg.group_openid not in self.s.allowed_groups:
            return False
        return msg.actor_openid in self.s.admin_openids

    # ---- 状态查询 ------------------------------------------------------------

    async def query(self, details: bool = False) -> MCStatus:
        """details=True 时（「状态」指令）再查 MSPT/TPS 与 frp 隧道延迟；监控轮询不查。"""
        try:
            r = await slp.ping(self.s.query_host, self.s.query_port, self.s.query_timeout)
        except TimeoutError:
            return MCStatus(online=False, error="查询超时")
        except Exception as exc:
            return MCStatus(online=False, error=f"{type(exc).__name__}: {exc}")
        # 样本里关闭了「在服务器列表中显示」的玩家（carpet 假人默认如此）只显示为 Anonymous Player；
        # 用会话追踪的在线名单补全，并把假人单独列出
        names = [n for n in r.names if n and n != ANONYMOUS]
        fakes: list[str] = []
        if self.roster is not None:
            with contextlib.suppress(Exception):
                real, fakes = await self.roster()
                names = [n for n in names if n not in fakes]
                names += [n for n in real if n not in names]
        tick: perf.TickStats | None = None
        tunnel: perf.TunnelRtt | None = None
        if details:
            tick, tunnel = await asyncio.gather(self._tick(), self._tunnel())
        return MCStatus(
            online=True,
            players_online=r.online,
            players_max=r.max,
            player_names=names,
            fake_names=fakes,
            version=r.version or "",
            tunnel=tunnel,
            tick=tick,
        )

    async def _tick(self) -> perf.TickStats | None:
        if self.channel is None:
            return None
        with contextlib.suppress(Exception):
            return await perf.tick_stats(self.channel.run)
        return None

    async def _tunnel(self) -> perf.TunnelRtt | None:
        if self.s.frp_server_port <= 0:
            return None
        with contextlib.suppress(Exception):
            return await perf.frp_tunnel_rtt(self.s.frp_server_port)
        return None

    async def cmd_perf(self) -> str:
        """主机资源占用。属基础设施信息，只对管理员开放（调用方把关）。"""
        pid = await self.pid_lookup()
        lines: list[str] = []
        if pid:
            cpu = await asyncio.to_thread(_cpu_percent, pid)
            rss = _rss_mb(pid)
            if cpu is not None:
                lines.append(f"CPU: {cpu:.1f}%")
            if rss is not None:
                lines.append(f"Java 实际内存: {rss}MB")
        else:
            lines.append("服务端进程未运行")
        world = self.game_dir / "world"
        if world.is_dir():
            size = await asyncio.to_thread(_du, world)
            lines.append(f"世界大小: {_human(size)}")
        du = shutil.disk_usage(self.game_dir)
        lines.append(
            f"磁盘: 已用 {_human(du.used)} / 总计 {_human(du.total)} (剩余 {_human(du.free)})"
        )
        return f"📊 服务器性能\n【{self.s.display_host}】\n" + "\n".join(lines)

    async def cmd_mods(self) -> str:
        mods = self.game_dir / "mods"
        try:
            files = sorted(p.name for p in mods.glob("*.jar"))
        except OSError:
            files = []
        if not files:
            return "（未解析到 Mod 列表）"
        names = [re.sub(r"[-_]v?\d[\w.+]*$", "", f[:-4]) for f in files]
        total = len(names)
        shown = "\n".join(f"· {n}" for n in names[:50])
        more = f"\n…… 共 {total} 个" if total > 50 else ""
        return f"🧩 已安装 Mod（{total} 个）\n{shown}{more}"

    def cmd_join(self, status: MCStatus) -> str:
        # 地址有意对全群开放：白名单才是入场控制，且地址早已在公开扫描库中
        lines = ["🎮 加入服务器", f"📍 地址：{self.s.display_host}"]
        if status.online and status.version:
            lines.append(f"📦 版本：Minecraft {status.version}（Java 版）")
        if self.s.join_note:
            lines += ["", self.s.join_note.strip()]
        return "\n".join(lines)

    # ---- 存储 ----------------------------------------------------------------

    async def add_group(self, openid: str) -> None:
        if not openid:
            return
        stmt = sqlite_insert(db.bot_groups).values(openid=openid, added_at=datetime.now(UTC))
        async with self.engine.begin() as conn:
            await conn.execute(stmt.on_conflict_do_nothing(index_elements=["openid"]))

    async def remove_group(self, openid: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(delete(db.bot_groups).where(db.bot_groups.c.openid == openid))

    async def groups(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(db.bot_groups))).mappings().all()
        return [dict(r) for r in rows]

    async def _apps(self) -> dict[str, dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(db.bot_applications))).mappings().all()
        return {r["name"]: {"openid": r["openid"], "ts": r["ts"]} for r in rows}

    async def _app_get(self, name: str) -> dict[str, Any] | None:
        a = db.bot_applications.c
        async with self.engine.connect() as conn:
            r = (
                (
                    await conn.execute(
                        select(db.bot_applications).where(func.lower(a.name) == name.lower())
                    )
                )
                .mappings()
                .first()
            )
        return dict(r) if r else None

    async def _app_pop(self, name: str) -> dict[str, Any] | None:
        rec = await self._app_get(name)
        if rec is not None:
            async with self.engine.begin() as conn:
                await conn.execute(
                    delete(db.bot_applications).where(db.bot_applications.c.name == rec["name"])
                )
        return rec

    async def _member_bind(self, openid: str, name: str) -> None:
        if not openid:
            return
        stmt = sqlite_insert(db.bot_members).values(
            openid=openid, name=name, bound_at=datetime.now(UTC)
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt.on_conflict_do_nothing(index_elements=["openid", "name"]))

    async def member_names(self, openid: str) -> list[str]:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(db.bot_members.c.name).where(db.bot_members.c.openid == openid)
                )
            ).all()
        return [r[0] for r in rows]

    async def audit(self, actor: str, action: str, target: str, result: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(db.audit_log).values(
                    ts=datetime.now(UTC),
                    actor=actor,
                    channel="qq",
                    action=f"whitelist.{action}",
                    target=target,
                    result=result,
                    detail={},
                )
            )

    # ---- 白名单 --------------------------------------------------------------

    async def reload_and_verify(self) -> set[str] | None:
        """让服务端重载白名单并回读 `whitelist list`。原生 add 会静默写错，必须以服务端名单为准。"""
        if self.channel is None:
            return None
        await self.channel.run("whitelist reload")
        out = await self.channel.run("whitelist list")
        return parse_whitelist_list(out) if out else None

    async def do_add(self, name: str, actor: str, source: str) -> str:
        if self.wl is None:
            log.error("未配置 whitelist.json，无法添加白名单")
            return T.MSG_INTERNAL_ERR
        try:
            await asyncio.to_thread(self.wl.add, name)
        except WhitelistError as exc:
            if exc.detail:
                log.error("白名单添加失败 name=%s: %s", name, exc.detail)
            await self.audit(actor, source, name, f"rejected:{exc}")
            return f"❌ {exc}"
        confirmed = await self.reload_and_verify()
        if confirmed is None:
            await self.audit(actor, source, name, "written-unverified")
            return f"⚠️ 已写入 {name}，但未能从服务端确认，请管理员核对后台。"
        if name in confirmed:
            await self.audit(actor, source, name, "ok")
            return (
                f"✅ 已加入白名单：{name}\n"
                f"服务端已确认，当前共 {len(confirmed)} 人。\n"
                f"提示：进服用的 ID 必须与此完全一致（区分大小写）。"
            )
        await self.audit(actor, source, name, "verify-mismatch")
        log.error("白名单写入后服务端未加载：%s", name)
        return f"⚠️ {name} 写入后未被服务端加载，请管理员核对后台。"

    async def do_remove(self, name: str, actor: str) -> str:
        if self.wl is None:
            return T.MSG_INTERNAL_ERR
        try:
            removed = await asyncio.to_thread(self.wl.remove, name)
        except WhitelistError as exc:
            if exc.detail:
                log.error("移除白名单失败 name=%s: %s", name, exc.detail)
                return T.MSG_INTERNAL_ERR
            return f"❌ {exc}"
        await self._grant_close(removed, "cancelled")
        confirmed = await self.reload_and_verify()
        await self.audit(actor, "remove", removed, "ok")
        tail = (
            f"\n服务端已确认，当前共 {len(confirmed)} 人。"
            if confirmed is not None
            else "\n⚠️ 未能从服务端确认，请手动核对。"
        )
        return f"🗑 已移出白名单：{removed}{tail}"

    async def approve(self, name: str, actor: str) -> str:
        rec = await self._app_get(name)
        target = rec["name"] if rec else name
        if not validate_name(target):
            return "❌ 游戏ID 不合法。"
        reply = await self.do_add(target, actor, "approve")
        if reply.startswith(("✅", "⚠️")):
            popped = await self._app_pop(target)
            if popped and popped.get("openid"):
                await self._member_bind(popped["openid"], target)
                deadline = await self._grant_open(target, popped["openid"])
                if deadline is not None:
                    reply += (
                        f"\n⏰ 请在 {_local_hm(deadline)} 前进服并完成注册（/register），"
                        "逾期未注册会自动移出白名单，需要重新申请。"
                    )
        if rec is None and reply.startswith("✅"):
            reply += "\n（该 ID 无待审申请，已按直接添加处理）"
        return reply

    async def reject(self, name: str, actor: str) -> str:
        rec = await self._app_pop(name)
        if rec is None:
            return f"📭 没有找到 `{name}` 的待审申请。"
        await self.audit(actor, "reject", rec["name"], "ok")
        return f"🚫 已驳回 `{rec['name']}` 的白名单申请。"

    # ---- 获批后注册期限 ------------------------------------------------------

    async def _grant_open(self, name: str, openid: str | None) -> datetime | None:
        """登记一条待注册的获批记录，返回截止时间；期限为 0 时不登记。"""
        hours = self.s.activation_hours
        if hours <= 0:
            return None
        now = datetime.now(UTC)
        deadline = now + timedelta(hours=hours)
        row = {
            "name": name, "openid": openid, "granted_at": now, "deadline": deadline,
            "status": "pending", "resolved_at": None,
        }  # fmt: skip
        stmt = sqlite_insert(db.whitelist_grants).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["name"], set_={k: v for k, v in row.items() if k != "name"}
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)
        return deadline

    async def _grant_close(
        self, name: str, status: str, now: datetime | None = None, only_pending: bool = True
    ) -> None:
        g = db.whitelist_grants.c
        cond = func.lower(g.name) == name.lower()
        if only_pending:
            cond = cond & (g.status == "pending")
        async with self.engine.begin() as conn:
            await conn.execute(
                update(db.whitelist_grants)
                .where(cond)
                .values(status=status, resolved_at=now or datetime.now(UTC))
            )

    async def grants(self, pending_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        g = db.whitelist_grants.c
        q = select(db.whitelist_grants).order_by(desc(g.granted_at)).limit(limit)
        if pending_only:
            q = q.where(g.status == "pending")
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def _played_since(self, name: str, since: datetime) -> bool:
        s = db.player_sessions.c
        async with self.engine.connect() as conn:
            hit = (
                await conn.execute(
                    select(s.id)
                    .where(func.lower(s.name) == name.lower())
                    .where(s.fake.is_(False))
                    .where(s.login_at >= since)
                    .limit(1)
                )
            ).first()
        return hit is not None

    async def activation_sweep(self, now: datetime | None = None) -> list[str]:
        """结清待注册记录：已进服并注册 → activated；逾期 → 移出白名单（expired）。

        返回本轮被移出的名字。读不到 EasyAuth 数据库或白名单时整轮跳过，宁可晚移也不误移。
        """
        now = now or datetime.now(UTC)
        pending = await self.grants(pending_only=True, limit=1000)
        if not pending or self.wl is None:
            return []
        registered: set[str] | None = None
        if self.registered is not None:
            registered = await asyncio.to_thread(self.registered)
            if registered is None:
                log.warning("读不到 EasyAuth 数据库，本轮跳过白名单注册期限检查")
                return []
        try:
            listed = {n.lower() for n in await asyncio.to_thread(self.wl.names)}
        except WhitelistError as exc:
            log.error("读取白名单失败，跳过注册期限检查：%s", exc.detail or exc)
            return []
        online: set[str] = set()
        if self.roster is not None:
            with contextlib.suppress(Exception):
                real, fake = await self.roster()
                online = {n.lower() for n in [*real, *fake]}
        expired: list[str] = []
        for r in pending:
            name, low = r["name"], r["name"].lower()
            if low not in listed:  # 已被手动移出
                await self._grant_close(name, "cancelled", now)
                continue
            if await self._played_since(low, _aware(r["granted_at"])) and (
                registered is None or low in registered
            ):
                await self._grant_close(name, "activated", now)
                continue
            # 期限内，或人正在服里（多半正在注册）：再等等
            if self.s.activation_hours <= 0 or now < _aware(r["deadline"]) or low in online:
                continue
            reply = await self.do_remove(name, "system:activation")
            if not reply.startswith("🗑"):
                log.error("注册期限已过但移出白名单失败：%s（%s）", name, reply)
                continue
            await self._grant_close(name, "expired", now, only_pending=False)
            if r["openid"]:
                async with self.engine.begin() as conn:
                    await conn.execute(
                        delete(db.bot_members)
                        .where(db.bot_members.c.openid == r["openid"])
                        .where(db.bot_members.c.name == name)
                    )
            await self.audit("system:activation", "expire", name, "ok")
            log.info("获批 %s 小时内未注册，已移出白名单：%s", self.s.activation_hours, name)
            expired.append(name)
        if expired and self.ui_publish is not None:
            self.ui_publish(Event("whitelist_expired", "bot", {"names": expired}))
        return expired

    async def activation_loop(self, interval_s: float = 300.0) -> None:
        while True:
            try:
                await self.activation_sweep()
            except Exception:
                log.exception("白名单注册期限检查异常")
            await asyncio.sleep(interval_s)

    async def handle_whitelist_command(self, msg: Message, cmd: str, arg: str | None) -> str:
        actor = msg.actor_openid or "UNKNOWN"
        admin = self.is_admin(msg)

        if cmd == "myid":
            if self.s.admin_openids and not admin:
                return T.MSG_ADMIN_ONLY
            if not msg.actor_openid:
                return "⚠️ 未能取得你的 openid（平台未下发该字段）。"
            if not self.s.admin_openids:
                return f"🆔 你的 openid：\n{msg.actor_openid}\n请把它写入机器人配置以成为管理员。"
            return f"🆔 你的 openid：\n{msg.actor_openid}"

        if cmd == "my_wl":
            waiting = {
                g["name"]: g["deadline"]
                for g in await self.grants(pending_only=True)
                if g["openid"] == actor
            }
            names = [n for n in await self.member_names(actor) if n not in waiting]
            pend = [n for n, r in (await self._apps()).items() if r["openid"] == actor]
            lines = []
            if names:
                lines.append("✅ 已获批：" + "、".join(names))
            for n, dl in waiting.items():
                lines.append(f"⏰ 待注册：{n}（请在 {_local_hm(dl)} 前进服完成 /register）")
            if pend:
                lines.append("⏳ 待审中：" + "、".join(pend))
            if not lines:
                return (
                    "📭 你名下没有申请记录。\n"
                    "如需进服请发送：@我 申请白名单 <你的游戏ID>\n"
                    "若你在白名单上线前就已在服内，直接进服即可。"
                )
            return "🎫 你的白名单状态\n" + "\n".join(lines)

        if cmd in T.ADMIN_ONLY and not admin:
            if not self.s.admin_openids:
                return "⛔ 机器人尚未完成管理员配置，该指令暂不可用。"
            await self.audit(actor, cmd, arg or "-", "denied-not-admin")
            return T.MSG_ADMIN_ONLY + "\n如需进服请发送：@我 申请白名单 <你的游戏ID>"

        if cmd == "alerts":
            if msg.group_openid not in self.s.alert_groups:
                return "📭 本群不是告警群。如需在这里查看告警，请先发送：@我 设为告警群"
            self._alert_hinted[msg.group_openid] = datetime.now(UTC)
            return await self.alerts_text()

        if cmd in ("alert_on", "alert_off"):
            if self.alert_group_hook is None or not msg.group_openid:
                return "⚠️ 告警推送暂不可用。"
            on = cmd == "alert_on"
            changed = await self.alert_group_hook(msg.group_openid, on)
            await self.audit(
                actor, cmd, T.mask_openid(msg.group_openid), "ok" if changed else "noop"
            )
            if on:
                return (
                    "🔔 本群已设为告警群：服务器严重安全告警会推送到这里。"
                    if changed
                    else "🔔 本群已经是告警群。"
                )
            return "🔕 本群已取消告警推送。" if changed else "🔕 本群原本就不是告警群。"

        if self.s.allowed_groups and msg.group_openid not in self.s.allowed_groups:
            return "⛔ 本群未被授权使用该功能。"
        if self.wl is None:
            return T.MSG_INTERNAL_ERR

        if cmd == "wl_list":
            try:
                names = self.wl.names()
            except WhitelistError as exc:
                log.error("读取白名单失败：%s", exc.detail or exc)
                return T.MSG_INTERNAL_ERR
            if not names:
                return "📋 白名单为空。"
            return f"📋 白名单（{len(names)} 人）\n" + "\n".join(f"· {n}" for n in names)

        if cmd == "pending":
            apps = await self._apps()
            if not apps:
                return "📭 当前没有待审申请。"
            lines = []
            for n, rec in sorted(apps.items(), key=lambda kv: kv[1]["ts"]):
                when = _aware(rec["ts"]).astimezone().strftime("%m-%d %H:%M")
                lines.append(f"· {n}（{when}）")
            return (
                f"📬 待审申请（{len(apps)} 条）\n"
                + "\n".join(lines)
                + "\n━━━━━━━━━━━━\n批准：@我 批准 <游戏ID>"
            )

        if cmd == "apply":
            name = arg or ""
            if not validate_name(name):
                return "❌ 游戏ID 不合法。\n只允许字母 / 数字 / 下划线，长度 3-16，且区分大小写。"
            try:
                if name in self.wl.names():
                    return f"✅ `{name}` 已在白名单中，直接进服即可。"
            except WhitelistError as exc:
                log.error("读取白名单失败：%s", exc.detail or exc)
                return T.MSG_INTERNAL_ERR
            exist = await self._app_get(name)
            if exist:
                return f"⏳ `{exist['name']}` 的申请已在待审队列中，请等管理员处理。"
            queue = await self._apps()
            if len(queue) >= self.s.max_pending_total:
                return "⏳ 当前申请较多，请稍后再试或联系管理员。"
            mine = sum(1 for r in queue.values() if r["openid"] == actor)
            if mine >= self.s.max_pending_per_user:
                await self.audit(actor, "apply", name, "denied-rate-limit")
                return f"⏳ 你已有 {mine} 条待审申请，请等管理员处理后再提交。"
            async with self.engine.begin() as conn:
                await conn.execute(
                    insert(db.bot_applications).values(
                        name=name, openid=actor, ts=datetime.now(UTC)
                    )
                )
            await self.audit(actor, "apply", name, "pending")
            rule = T.activation_rule(self.s.activation_hours)
            return f"📨 已登记白名单申请：{name}\n请等管理员审核。" + (f"\n{rule}" if rule else "")

        if cmd == "approve":
            return await self.approve(arg or "", actor)
        if cmd == "reject":
            return await self.reject(arg or "", actor)
        if cmd == "wl_add":
            return await self.do_add(arg or "", actor, "admin-add")
        if cmd == "wl_remove":
            return await self.do_remove(arg or "", actor)
        return "未识别的白名单指令。"

    # ---- 消息入口 ------------------------------------------------------------

    async def handle_message(self, msg: Message, simulated: bool = False) -> tuple[str, str]:
        if not simulated:
            await self.add_group(msg.group_openid)
        admin = self.is_admin(msg)
        raw = (msg.content or "").strip()
        strict = T.parse_strict_command(msg.content or "")
        if strict:
            cmd, arg = strict
            reply = await self.handle_whitelist_command(msg, cmd, arg)
        else:
            text = raw.lower()
            if any(k in text for k in T.HELP_KEYWORDS):
                cmd, reply = "help", T.help_text(admin, self.s.activation_hours, self.s.name)
            elif any(k in text for k in T.PERF_KEYWORDS):
                cmd = "perf"
                reply = await self.cmd_perf() if admin else T.MSG_ADMIN_ONLY
            elif any(k in text for k in T.MODS_KEYWORDS):
                cmd, reply = "mods", await self.cmd_mods()
            elif any(k in text for k in T.JOIN_KEYWORDS):
                cmd, reply = "join", self.cmd_join(await self.query())
            elif any(k in text for k in T.PLAYERS_KEYWORDS):
                cmd, reply = "players", (await self.query()).players_text()
            elif not text or any(k in text for k in T.STATUS_KEYWORDS):
                cmd = "status"
                reply = f"【{self.s.display_host}】\n{(await self.query(details=True)).summary()}"
            else:
                cmd, reply = (
                    "unknown",
                    "未识别的指令。\n" + T.help_text(admin, self.s.activation_hours, self.s.name),
                )
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(db.bot_interactions).values(
                    ts=datetime.now(UTC),
                    group_openid=msg.group_openid,
                    openid=msg.actor_openid,
                    admin=admin,
                    cmd=cmd,
                    input=raw[:2000],
                    output=reply[:4000],
                    simulated=simulated,
                )
            )
        log.info("QQ 消息 openid=%s admin=%s cmd=%s", T.mask_openid(msg.actor_openid), admin, cmd)
        if not simulated and self.ui_publish is not None:
            self.ui_publish(Event("bot_interaction", "bot", {"cmd": cmd}))
        if cmd != "alerts" and msg.group_openid in self.s.alert_groups:
            since = self._alert_hinted.get(msg.group_openid, self._started)
            n = await self._open_alerts_since(since)
            if n:
                self._alert_hinted[msg.group_openid] = datetime.now(UTC)
                reply += f"\n━━━━━━━━━━━━\n🔔 有 {n} 条新的安全告警，发送「@我 告警」查看"
        return cmd, reply

    async def _open_alerts_since(self, since: datetime) -> int:
        a = db.alerts.c
        cond = (
            a.status == "open",
            a.historical.is_(False),
            a.severity != "info",
            a.last_ts > since,
        )
        async with self.engine.connect() as conn:
            n = (
                await conn.execute(select(func.count()).select_from(db.alerts).where(*cond))
            ).scalar()
        return int(n or 0)

    async def alerts_text(self, limit: int = 5) -> str:
        a = db.alerts.c
        cond = (a.status == "open", a.historical.is_(False))
        async with self.engine.connect() as conn:
            q = select(db.alerts).where(*cond).order_by(desc(a.last_ts)).limit(limit)
            rows = (await conn.execute(q)).mappings().all()
            total = int(
                (
                    await conn.execute(select(func.count()).select_from(db.alerts).where(*cond))
                ).scalar()
                or 0
            )
        if not rows:
            return "✅ 当前没有未处理的安全告警。"
        icon = {"critical": "🚨", "warning": "⚠️"}
        lines = []
        for r in rows:
            local = _aware(r["last_ts"]).astimezone()
            times = f"（×{r['count']}）" if r["count"] > 1 else ""
            lines.append(f"{icon.get(r['severity'], 'ℹ️')} {local:%m-%d %H:%M} {r['title']}{times}")
        more = f"\n…另有 {total - len(rows)} 条" if total > len(rows) else ""
        return (
            f"🛡 未处理告警 {total} 条\n"
            + "\n".join(lines)
            + more
            + "\n详情与确认请到管理面板「安全」页"
        )

    async def interactions(self, limit: int = 100, q: str | None = None) -> list[dict[str, Any]]:
        i = db.bot_interactions.c
        stmt = select(db.bot_interactions).order_by(desc(i.id)).limit(limit)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(i.input.like(like), i.output.like(like), i.cmd.like(like)))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def applications(self) -> list[dict[str, Any]]:
        return [
            {"name": n, **r}
            for n, r in sorted((await self._apps()).items(), key=lambda kv: kv[1]["ts"])
        ]

    # ---- 主动推送 / 监控 -----------------------------------------------------

    async def push(self, content: str, groups: set[str] | None = None) -> int:
        if self.pusher is None:
            return 0
        if datetime.now().date() != self._pushes_day:
            self._pushes_day, self.pushes_today = datetime.now().date(), 0
        targets = groups if groups is not None else {g["openid"] for g in await self.groups()}
        if not targets:
            log.warning("没有已记录的群 openid，无法主动推送")
            return 0
        n = await self.pusher(content, targets)
        self.pushes_today += n
        return n

    def read_planned_actor(self) -> str | None:
        if self.planned_source is not None:
            return self.planned_source()
        if self.s.watchdog_state_file is None:
            return None
        try:
            raw = self.s.watchdog_state_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        parts = raw.split(":")
        state, who = [*parts, "", ""][:2]
        return who if state != "running" and who in self.s.planned_actors else None

    async def monitor_loop(self) -> None:
        while True:
            try:
                await self.handle_transition(await self.query())
            except Exception:
                log.exception("监控循环异常")
            await asyncio.sleep(self.s.monitor_interval)

    async def maintenance_watcher(self, interval_s: float = 10.0) -> None:
        p = self.s.maintenance_signal_file
        if p is None:
            return
        while True:
            try:
                if p.exists():
                    raw = p.read_text(encoding="utf-8").strip()
                    with contextlib.suppress(FileNotFoundError):
                        p.unlink()  # 一次性信号，读后即删
                    try:
                        data = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        data = {}
                    await self.begin_maintenance(data if isinstance(data, dict) else {})
            except Exception:
                log.exception("维护信号处理异常")
            await asyncio.sleep(interval_s)

    async def begin_maintenance(self, data: dict[str, Any]) -> None:
        warn = data.get("warn_minutes")
        now = time.time()
        announce = now - self._last_maint_announce >= self.s.maintenance_cooldown
        self._maint_until = float(data.get("until", now + 1200))
        self._maint_mode, self._maint_saw_down = True, False
        if not announce:
            return
        self._last_maint_announce = now
        when = f"将在 {warn} 分钟后" if warn else "即将"
        await self.push(
            f"🔧 [自动维护] 服务器{when}重启维护\n【{self.s.display_host}】\n"
            f"预计随后数分钟内自动恢复，请及时下线以免数据丢失～"
        )

    async def handle_transition(self, status: MCStatus) -> None:
        host, active, threshold = self.s.display_host, self.s.active_alert, self.s.fail_threshold
        if self._maint_mode:
            if time.time() > self._maint_until:
                self._maint_mode = False
            else:
                if status.online:
                    self._fail_count, self._last_online = 0, True
                    if self._maint_saw_down:
                        self._maint_mode = False
                        if active:
                            await self.push(
                                f"✅ 维护完成，Minecraft 服务器已恢复\n【{host}】\n{status.summary()}"
                            )
                else:
                    self._maint_saw_down, self._last_online, self._fail_count = True, False, 0
                return

        if status.online:
            was_down = self._last_online is False
            self._fail_count = 0
            if self._last_online is None:
                self._last_online = True
            elif was_down:
                self._last_online = True
                if active:
                    prefix = (
                        "✅ 维护完成，Minecraft 服务器已恢复"
                        if self._in_maintenance
                        else "✅ Minecraft 服务器已恢复"
                    )
                    await self.push(f"{prefix}\n【{host}】\n{status.summary()}")
                self._in_maintenance = False
        else:
            self._fail_count += 1
            if self._fail_count >= threshold and self._last_online is not False:
                self._last_online = False
                planned = self.read_planned_actor()
                self._in_maintenance = bool(planned)
                if active:
                    if planned:
                        await self.push(
                            f"🔧 [计划维护] Minecraft 服务器维护重启中\n【{host}】\n"
                            f"服务器正在按计划重启，预计几分钟内自动恢复，请稍候。"
                        )
                    else:
                        await self.push(
                            f"🔴 Minecraft 服务器疑似宕机\n【{host}】\n"
                            f"连续 {threshold} 次无法连接（{status.error}）"
                        )

    async def push_security_alert(self, alert: dict[str, Any]) -> None:
        """严重安全告警只推给配置的管理群（玩家群全员可见，不能广播 IP 等细节）。"""
        if alert.get("severity") != "critical" or not self.s.alert_groups:
            return
        await self.push(f"🚨 [安全告警] {alert['title']}\n{alert['detail']}", self.s.alert_groups)

    def status(self) -> dict[str, Any]:
        return {
            "pushes_today": self.pushes_today,
            "maintenance": {
                "active": self._maint_mode,
                "until": datetime.fromtimestamp(self._maint_until, UTC).isoformat()
                if self._maint_mode
                else None,
            },
            "monitor": {"last_online": self._last_online, "fail_count": self._fail_count},
        }


# ---- 进程指标 ----------------------------------------------------------------


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def _local_hm(ts: datetime) -> str:
    return _aware(ts).astimezone().strftime("%m-%d %H:%M")


def _cpu_percent(pid: int, interval: float = 0.5) -> float | None:
    def ticks() -> int | None:
        try:
            parts = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return int(parts[11]) + int(parts[12])  # utime + stime
        except (OSError, IndexError, ValueError):
            return None

    a = ticks()
    time.sleep(interval)
    b = ticks()
    if a is None or b is None:
        return None
    hz = os.sysconf("SC_CLK_TCK")
    return (b - a) / hz / interval * 100


def _rss_mb(pid: int) -> int | None:
    try:
        for ln in Path(f"/proc/{pid}/status").read_text().splitlines():
            if ln.startswith("VmRSS:"):
                return int(ln.split()[1]) // 1024
    except (OSError, ValueError):
        return None
    return None


def _du(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
    return total


def _human(n: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}T"
