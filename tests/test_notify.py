"""M4：面板通知设置（存取 / 校验 / 路由 / 限流 / 测试按钮 / API / QQ「设为告警群」）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcops.api import Services
from mcops.db import open_db
from mcops.notify import NotifyHub
from mcops.notifysettings import AlertGroup, EmailSettings, NotifySettings, QQSettings, RuleOverride
from mcops.pipeline import Pipeline
from mcops.security import SecurityEngine
from tests.test_api_m1 import CST, ctx  # noqa: F401  (fixture 复用)
from tests.test_bot import ADMIN, GROUP, M, bot  # noqa: F401

GROUP_A = "ADMINGROUPOPENID01"


def _alert(sev: str, title: str = "t") -> dict[str, Any]:
    return {
        "severity": sev,
        "rule": "night_login",
        "title": title,
        "detail": "d",
        "subject": "x",
        "first_ts": "now",
    }


class Rec:
    def __init__(self) -> None:
        self.emails: list[tuple[str, str]] = []
        self.qq: list[tuple[str, set[str]]] = []
        self.fail_email: Exception | None = None

    def send_email(self, e: EmailSettings, password: str, subject: str, body: str) -> None:
        if self.fail_email:
            raise self.fail_email
        self.emails.append((subject, password))

    async def push(self, content: str, groups: set[str]) -> int:
        self.qq.append((content, groups))
        return len(groups)


@pytest.fixture
async def hub(tmp_path: Path) -> AsyncIterator[tuple[NotifyHub, Rec]]:
    engine = await open_db(tmp_path / "n.db")
    h, rec = NotifyHub(engine), Rec()
    h.send_email, h.qq_push = rec.send_email, rec.push
    yield h, rec
    await engine.dispose()


def _configured() -> NotifySettings:
    return NotifySettings(
        email=EmailSettings(enabled=True, host="smtp.qq.com", sender="a@qq.com", to=["b@qq.com"]),
        qq=QQSettings(groups=[AlertGroup(openid=GROUP_A, label="管理群")]),
    )


async def test_seed_then_persisted(hub: tuple[NotifyHub, Rec], tmp_path: Path) -> None:
    h, _ = hub
    assert await h.load(_configured(), "pw1") is True
    assert h.has_password
    h2 = NotifyHub(h.engine)
    assert await h2.load(NotifySettings(), None) is False  # 已有库内设置：yaml 不再覆盖
    assert h2.settings.email.host == "smtp.qq.com" and h2.has_password


async def test_default_routing(hub: tuple[NotifyHub, Rec]) -> None:
    h, rec = hub
    await h.load(_configured(), "pw")
    await h(_alert("critical"))
    await h(_alert("warning"))
    await h(_alert("info"))
    assert len(rec.emails) == 2  # 严重 + 警告
    assert (
        len(rec.qq) == 1 and rec.qq[0][1] == {GROUP_A} and "严重告警" in rec.qq[0][0]
    )  # 只有严重进 QQ


async def test_email_rate_limit_and_failure_isolated(hub: tuple[NotifyHub, Rec]) -> None:
    h, rec = hub
    s = _configured()
    s.email.max_per_hour = 2
    await h.load(s, "pw")
    for i in range(4):
        await h(_alert("critical", f"t{i}"))
    assert len(rec.emails) == 2 and h.stats()["email_dropped"] == 2
    assert len(rec.qq) == 4  # 邮件限流不影响 QQ
    rec.fail_email = OSError("smtp down")
    h._email_rl = type(h._email_rl)()
    await h(_alert("critical"))
    assert "smtp down" in (h.stats()["last_error"]["email"] or "") and len(rec.qq) == 5


async def test_save_validation_and_password_semantics(hub: tuple[NotifyHub, Rec]) -> None:
    h, _ = hub
    await h.load(NotifySettings(), None)
    with pytest.raises(ValueError, match="授权码"):
        await h.save(_configured())  # 启用邮件却没有密码
    await h.save(_configured(), "secret")
    assert h.has_password
    await h.save(_configured(), None)  # None = 不修改
    assert h.has_password
    s = _configured()
    s.email.enabled = False
    await h.save(s, "")  # "" = 清除
    assert not h.has_password
    with pytest.raises(ValueError, match="收件地址不合法"):
        EmailSettings(to=["not-an-email"])


async def test_test_button(hub: tuple[NotifyHub, Rec]) -> None:
    h, rec = hub
    await h.load(NotifySettings(), None)
    ok, detail = await h.test("email")
    assert not ok and "尚未配置" in detail
    await h.save(_configured(), "pw")
    assert (await h.test("email"))[0] and len(rec.emails) == 1
    ok, detail = await h.test("qq")
    assert ok and "1 个群" in detail
    h.qq_ready = lambda: False
    ok, detail = await h.test("qq")
    assert not ok and "未连接" in detail


async def test_set_alert_group(hub: tuple[NotifyHub, Rec]) -> None:
    h, _ = hub
    await h.load(NotifySettings(), None)
    assert await h.set_alert_group("GROUPOPENID12345", True) is True
    assert await h.set_alert_group("GROUPOPENID12345", True) is False
    assert [g.openid for g in h.settings.qq.groups] == ["GROUPOPENID12345"]
    assert await h.set_alert_group("GROUPOPENID12345", False) is True
    assert h.settings.qq.groups == []


async def test_rule_overrides_apply_to_security_engine(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    engine = await open_db(tmp_path / "s.db")
    sent: list[dict[str, Any]] = []

    async def notifier(a: dict[str, Any]) -> None:
        sent.append(a)

    secu = SecurityEngine(engine, CST, notifier=notifier)
    secu.cfg.severity_overrides["night_login"] = "info"
    secu.cfg.disabled_rules.add("new_ip")
    now = datetime.now(UTC)
    await secu.raise_alert("night_login", now, "n", "d", "p", {})
    await secu.raise_alert("new_ip", now, "n", "d", "p", {})
    await secu.raise_alert("fingerprint_mismatch", now, "f", "d", "q", {}, severity="critical")
    assert [(a["rule"], a["severity"]) for a in sent] == [
        ("night_login", "info"),
        ("fingerprint_mismatch", "critical"),
    ]
    await engine.dispose()


async def test_api_roundtrip(ctx: tuple[httpx.AsyncClient, Services, Pipeline]) -> None:  # noqa: F811
    c, svc, _ = ctx
    rec = Rec()
    svc.notify = NotifyHub(svc.engine)
    svc.notify.send_email, svc.notify.qq_push = rec.send_email, rec.push
    await svc.notify.load(NotifySettings(), None)

    r = await c.get("/api/settings/notify")
    assert r.status_code == 200
    v = r.json()
    assert v["email"]["has_password"] is False and "password" not in v["email"]
    assert any(x["id"] == "flight_suspect" and x["enabled"] for x in v["rules"])

    body = {
        "email": {**_configured().email.model_dump(), "password": None},
        "qq": {
            "enabled": True,
            "severities": ["critical"],
            "groups": [{"openid": GROUP_A, "label": "管理"}],
        },
        "rules": {"anticheat_kick": {"enabled": False, "severity": None}},
    }
    r = await c.put("/api/settings/notify", json=body)
    assert r.status_code == 422 and "授权码" in r.json()["detail"]
    body["email"]["password"] = "s3cret"
    r = await c.put("/api/settings/notify", json=body)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["email"]["has_password"] is True and "s3cret" not in r.text
    assert next(x for x in v["rules"] if x["id"] == "anticheat_kick")["enabled"] is False
    # 局部 rules 更新与原有覆盖合并；恢复默认的覆盖被清掉
    body["email"]["password"] = None
    body["rules"] = {"night_login": {"enabled": True, "severity": "info"}}
    v = (await c.put("/api/settings/notify", json=body)).json()
    assert {x["id"]: (x["enabled"], x["severity"]) for x in v["rules"]}["anticheat_kick"] == (
        False,
        None,
    )
    body["rules"] = {"anticheat_kick": {"enabled": True, "severity": None}}
    await c.put("/api/settings/notify", json=body)
    assert set(svc.notify.settings.rules) == {"night_login"}

    r = await c.put("/api/settings/notify", json={**body, "rules": {"nope": {"enabled": False}}})
    assert r.status_code == 422 and "未知规则" in r.json()["detail"]
    bad = {**body, "email": {**body["email"], "port": 70000}}
    assert (await c.put("/api/settings/notify", json=bad)).status_code == 422

    r = await c.post("/api/settings/notify/test", json={"channel": "email"})
    assert r.json()["ok"] is True and rec.emails[-1][1] == "s3cret"
    r = await c.post("/api/settings/notify/test", json={"channel": "qq"})
    assert r.json()["ok"] is True
    # CSRF：没有 token 的写请求被拒
    del c.headers["X-CSRF-Token"]
    assert (await c.put("/api/settings/notify", json=body)).status_code == 403


async def test_bot_alert_group_command(bot: Any) -> None:  # noqa: F811
    toggled: list[tuple[str, bool]] = []

    async def hook(openid: str, on: bool) -> bool:
        toggled.append((openid, on))
        return True

    bot.alert_group_hook = hook
    _, reply = await bot.handle_message(M("SOMEONE", "设为告警群"))
    assert "仅管理员" in reply and toggled == []
    _, reply = await bot.handle_message(M(ADMIN, "设为告警群"))
    assert "已设为告警群" in reply and toggled == [(GROUP, True)]
    _, reply = await bot.handle_message(M(ADMIN, "取消告警群"))
    assert "取消" in reply and toggled[-1] == (GROUP, False)


def test_rule_override_model() -> None:
    assert RuleOverride().enabled is True
    with pytest.raises(ValueError):
        RuleOverride.model_validate({"severity": "fatal"})


async def test_db_file_is_private(tmp_path: Path) -> None:
    engine = await open_db(tmp_path / "p.db")
    assert (tmp_path / "p.db").stat().st_mode & 0o777 == 0o600
    await engine.dispose()


async def test_qq_proactive_denied_message(hub: tuple[NotifyHub, Rec]) -> None:
    h, _ = hub
    await h.load(_configured(), "pw")

    async def denied(content: str, groups: set[str]) -> int:
        return 0

    h.qq_push = denied
    h.qq_error = lambda: "推送失败：主动消息失败, 无权限"
    ok, detail = await h.test("qq")
    assert not ok and "40034105" in detail and "主动消息开关" in detail


async def test_bot_alerts_command_and_hint(bot: Any) -> None:  # noqa: F811
    from datetime import UTC, datetime

    from mcops.security import SecurityEngine

    secu = SecurityEngine(bot.engine, CST)
    await secu.raise_alert("night_login", datetime.now(UTC), "凌晨登录：Alice", "d", "Alice", {})
    _, reply = await bot.handle_message(M(ADMIN, "告警"))
    assert "不是告警群" in reply
    bot.s.alert_groups = {GROUP}
    _, reply = await bot.handle_message(M(ADMIN, "帮助"))
    assert "有 1 条新的安全告警" in reply  # 被动回复里捎带提示
    _, reply = await bot.handle_message(M(ADMIN, "帮助"))
    assert "新的安全告警" not in reply  # 同一批只提示一次
    _, reply = await bot.handle_message(M(ADMIN, "告警"))
    assert "未处理告警 1 条" in reply and "凌晨登录：Alice" in reply
    _, reply = await bot.handle_message(M("SOMEONE", "告警"))
    assert "仅管理员" in reply
