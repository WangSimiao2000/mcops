"""面板可编辑的通知设置（M4，docs/01-design.md §10）：邮件 / QQ 管理群 / 规则开关与级别覆盖。

存在 kv 表（键 notify.settings）；SMTP 密码单独存 notify.smtp_password，API 只写不读。
首次启动时从 mcops.yaml 的 smtp / bot.alert_groups / security.disabled_rules 导入，之后以面板为准。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Sev = Literal["critical", "warning", "info"]
KEY, PW_KEY = "notify.settings", "notify.smtp_password"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailSettings(_M):
    enabled: bool = False
    host: str = ""
    port: int = Field(default=465, ge=1, le=65535)
    security: Literal["ssl", "starttls"] = "ssl"
    username: str = ""
    sender: str = ""
    to: list[str] = Field(default_factory=list)
    severities: list[Sev] = Field(default_factory=lambda: list[Sev](["critical", "warning"]))
    max_per_hour: int = Field(default=20, ge=1, le=500)

    @field_validator("to")
    @classmethod
    def _to(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for a in (x.strip() for x in v):
            if not a:
                continue
            if "@" not in a or " " in a or len(a) > 254:
                raise ValueError(f"收件地址不合法：{a}")
            if a not in out:
                out.append(a)
        return out


class AlertGroup(_M):
    openid: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(default="", max_length=40)


class QQSettings(_M):
    enabled: bool = True
    severities: list[Sev] = Field(default_factory=lambda: list[Sev](["critical"]))
    groups: list[AlertGroup] = Field(default_factory=list)


class RuleOverride(_M):
    enabled: bool = True
    severity: Sev | None = None


class NotifySettings(_M):
    email: EmailSettings = Field(default_factory=EmailSettings)
    qq: QQSettings = Field(default_factory=QQSettings)
    rules: dict[str, RuleOverride] = Field(default_factory=dict)

    def check_ready(self, has_password: bool) -> None:
        """保存前的跨字段校验；错误信息直接展示给面板用户。"""
        e = self.email
        if e.enabled:
            missing = [
                n for n, v in (("SMTP 主机", e.host), ("发件地址", e.sender)) if not v.strip()
            ]
            if missing:
                raise ValueError("启用邮件前请填写：" + "、".join(missing))
            if not e.to:
                raise ValueError("启用邮件前请至少填写一个收件人")
            if not has_password:
                raise ValueError("启用邮件前请填写 SMTP 授权码")
