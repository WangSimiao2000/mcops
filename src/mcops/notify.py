"""告警通知分发（M4）：面板铃铛始终收到全部告警；邮件与 QQ 管理群按面板「设置 → 通知」里的级别额外推送。

默认路由（docs/01-design.md §8）：严重 → QQ 管理群 + 邮件；警告 → 邮件。
每个渠道单独限流，单个渠道失败只记录 last_error，不影响告警入库和其他渠道。
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from collections import deque
from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.notifysettings import KEY, PW_KEY, AlertGroup, EmailSettings, NotifySettings

log = logging.getLogger(__name__)

SEV_ZH = {"critical": "严重", "warning": "警告", "info": "提示"}
_SEV_ICON = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
QQPush = Callable[[str, set[str]], Awaitable[int]]
_UNSET: Any = object()


class _RateLimit:
    def __init__(self) -> None:
        self._sent: deque[float] = deque()
        self.dropped = 0

    def count(self) -> int:
        now = time.monotonic()
        while self._sent and now - self._sent[0] > 3600:
            self._sent.popleft()
        return len(self._sent)

    def take(self, limit: int) -> bool:
        if self.count() >= limit:
            self.dropped += 1
            return False
        self._sent.append(time.monotonic())
        return True


def send_email(e: EmailSettings, password: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, e.sender, ", ".join(e.to)
    msg.set_content(body)
    ctx = ssl.create_default_context()
    user = e.username or e.sender
    if e.security == "ssl":
        with smtplib.SMTP_SSL(e.host, e.port, context=ctx, timeout=20) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(e.host, e.port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)


def _email_text(alert: dict[str, Any]) -> tuple[str, str]:
    sev = SEV_ZH.get(alert["severity"], alert["severity"])
    subject = f"[mc-ops][{sev}] {alert['title']}"
    body = (
        f"{alert['title']}\n\n{alert['detail']}\n\n"
        f"规则：{alert['rule']}\n时间：{alert.get('first_ts')}\n对象：{alert.get('subject') or '-'}\n\n"
        "在面板「安全」页查看与确认（ssh -L 8765:127.0.0.1:8765 mc-server 后打开 http://127.0.0.1:8765/security）。"
    )
    return subject, body


def _qq_text(alert: dict[str, Any]) -> str:
    icon = _SEV_ICON.get(alert["severity"], "🔔")
    sev = SEV_ZH.get(alert["severity"], alert["severity"])
    return f"{icon} [{sev}告警] {alert['title']}\n{alert['detail']}\n（详情见管理面板「安全」页）"


class NotifyHub:
    def __init__(
        self, engine: AsyncEngine, on_change: Callable[[NotifySettings], None] | None = None
    ) -> None:
        self.engine = engine
        self.settings = NotifySettings()
        self._password: str | None = None
        self.qq_push: QQPush | None = None
        self.qq_ready: Callable[[], bool] = lambda: self.qq_push is not None
        self.qq_error: Callable[[], str | None] = lambda: None
        self.on_change = on_change
        self._email_rl, self._qq_rl = _RateLimit(), _RateLimit()
        self.qq_sent_today, self._qq_day = 0, time.strftime("%Y-%m-%d")
        self.last_error: dict[str, str | None] = {"email": None, "qq": None}
        self.send_email = send_email  # 测试替换

    # ---- 存取 ----------------------------------------------------------------

    @property
    def has_password(self) -> bool:
        return bool(self._password)

    async def _kv_get(self, key: str) -> Any:
        async with self.engine.connect() as conn:
            return (await conn.execute(select(db.kv.c.value).where(db.kv.c.key == key))).scalar()

    async def _kv_put(self, key: str, value: Any) -> None:
        stmt = sqlite_insert(db.kv).values(key=key, value=value)
        async with self.engine.begin() as conn:
            await conn.execute(
                stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value})
            )

    async def load(self, seed: NotifySettings, seed_password: str | None) -> bool:
        """读取面板设置；库里没有则用 yaml 导入的 seed 初始化并落库。返回是否为首次导入。"""
        raw = await self._kv_get(KEY)
        first = raw is None
        if first:
            self.settings = seed
            await self._kv_put(KEY, seed.model_dump())
            if seed_password:
                await self._kv_put(PW_KEY, seed_password)
        else:
            try:
                self.settings = NotifySettings.model_validate(raw)
            except ValueError:
                log.exception("通知设置损坏，使用 yaml 默认值（未覆盖库里的数据）")
                self.settings = seed
        pw = await self._kv_get(PW_KEY)
        self._password = str(pw) if pw else None
        self._changed()
        return first

    async def save(self, new: NotifySettings, password: str | None = _UNSET) -> None:
        """password：_UNSET/None 不修改，"" 清除，其他为新密码。"""
        has_pw = self.has_password if password is _UNSET or password is None else bool(password)
        new.check_ready(has_pw)
        await self._kv_put(KEY, new.model_dump())
        if password is not _UNSET and password is not None:
            if password:
                await self._kv_put(PW_KEY, password)
            else:
                async with self.engine.begin() as conn:
                    await conn.execute(delete(db.kv).where(db.kv.c.key == PW_KEY))
            self._password = password or None
        self.settings = new
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self.settings)

    async def set_alert_group(self, openid: str, on: bool, label: str = "") -> bool:
        """QQ 群内「设为告警群 / 取消告警群」。返回是否有变化。"""
        groups = [g for g in self.settings.qq.groups if g.openid != openid]
        if on:
            old = next((g for g in self.settings.qq.groups if g.openid == openid), None)
            groups.append(old or AlertGroup(openid=openid, label=label))
        if len(groups) == len(self.settings.qq.groups) and on == any(
            g.openid == openid for g in self.settings.qq.groups
        ):
            return False
        new = self.settings.model_copy(deep=True)
        new.qq.groups = groups
        await self.save(new)
        return True

    # ---- 分发 ----------------------------------------------------------------

    async def __call__(self, alert: dict[str, Any]) -> None:
        sev = alert.get("severity", "info")
        s = self.settings
        tasks: list[Awaitable[Any]] = []
        if s.email.enabled and sev in s.email.severities and self._password:
            tasks.append(self._email(alert))
        if s.qq.enabled and sev in s.qq.severities and s.qq.groups:
            tasks.append(self._qq(_qq_text(alert)))
        if tasks:
            await asyncio.gather(*tasks)

    async def _email(self, alert: dict[str, Any]) -> None:
        e = self.settings.email
        if not self._email_rl.take(e.max_per_hour):
            log.warning("邮件告警超出每小时上限，已丢弃：%s", alert["title"])
            return
        subject, body = _email_text(alert)
        try:
            assert self._password is not None
            await asyncio.to_thread(self.send_email, e, self._password, subject, body)
            self.last_error["email"] = None
        except Exception as exc:
            self.last_error["email"] = f"{type(exc).__name__}: {exc}"[:300]
            log.error("邮件告警发送失败：%s", exc)

    async def _qq(self, content: str) -> int:
        if self.qq_push is None or not self.qq_ready():
            self.last_error["qq"] = "QQ 机器人未连接"
            return 0
        if not self._qq_rl.take(30):
            return 0
        try:
            n = await self.qq_push(content, {g.openid for g in self.settings.qq.groups})
        except Exception as exc:
            self.last_error["qq"] = f"{type(exc).__name__}: {exc}"[:300]
            log.error("QQ 告警推送失败：%s", exc)
            return 0
        today = time.strftime("%Y-%m-%d")
        if today != self._qq_day:
            self._qq_day, self.qq_sent_today = today, 0
        self.qq_sent_today += n
        if n:
            self.last_error["qq"] = None
        else:
            raw = self.qq_error() or ""
            if "40034105" in raw or "主动消息" in raw:
                self.last_error["qq"] = (
                    "该群关闭了「允许机器人主动发消息」（QQ 返回 40034105 无权限）。请群主/管理员在 QQ 群设置 → "
                    "群机器人 → 本机器人 中打开主动消息开关后再测试；关闭期间告警会在有人 @机器人 时附带提示，"
                    "也可发送「@机器人 告警」查看"
                )
            else:
                self.last_error["qq"] = raw or "推送未成功（机器人未在群内或 QQ 平台拒绝主动消息）"
        return n

    async def test(self, channel: str) -> tuple[bool, str]:
        alert = {
            "severity": "info",
            "rule": "test",
            "title": "测试通知",
            "detail": "这是一条来自 mc-ops 面板的测试消息，收到说明通知渠道配置正确。",
            "subject": None,
            "first_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if channel == "email":
            e = self.settings.email
            if not (e.host and e.sender and e.to and self._password):
                return False, "邮件尚未配置完整（主机、发件地址、收件人、授权码）"
            subject, body = _email_text(alert)
            try:
                await asyncio.to_thread(self.send_email, e, self._password, subject, body)
            except Exception as exc:
                self.last_error["email"] = f"{type(exc).__name__}: {exc}"[:300]
                return False, f"发送失败：{self.last_error['email']}"
            self.last_error["email"] = None
            return True, f"已发送到 {', '.join(e.to)}，请查收（可能在垃圾箱）"
        if channel == "qq":
            if not self.settings.qq.groups:
                return False, "还没有选择告警群"
            n = await self._qq(_qq_text(alert))
            if n:
                return True, f"已推送到 {n} 个群"
            return False, self.last_error["qq"] or "推送失败"
        return False, "未知渠道"

    def stats(self) -> dict[str, Any]:
        return {
            "email_sent_hour": self._email_rl.count(),
            "email_dropped": self._email_rl.dropped,
            "qq_sent_today": self.qq_sent_today,
            "last_error": dict(self.last_error),
        }
