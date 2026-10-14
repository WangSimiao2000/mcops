"""latest.log 行解析 → 结构化事件。

格式依据生产日志（Fabric 1.21.11 + EasyAuth 3.4.3 + proxy-protocol-support 1.1.0）：

    [17:50:31] [Server thread/INFO]: Dave[/1.2.3.4:46854] logged in with entity id 6996 at (…)
    [19:50:54] [Server thread/INFO]: FARMBOT2[local] logged in with entity id 35901 at (…)
    （[local] 是 carpet 假人）
    [02:49:07] [Server thread/INFO]: Yuki_Y lost connection: Flying is not enabled on this server
    [17:50:39] [Server thread/INFO]: [EasyAuth]: [LOGIN]: Player Dave is trying to login
    [17:50:39] [Server thread/INFO]: [EasyAuth]: [LOGIN]: Player Dave provide correct password
    [10:07:15] [Server thread/INFO]: Banned IP 198.51.100.96: <reason>
    [10:00:14] [Server thread/INFO]: Done (3.648s)! For help, type "help"

日志只有时分秒，日期由调用方给出（实时 tail 取当天；回放 .gz 取文件名日期）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

_COLOR_RE = re.compile("§.")
_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] \[([^\]]+)/([A-Z]+)\]: (.*)$")

_NAME = r"[A-Za-z0-9_]{1,16}"
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "player_login",
        re.compile(
            rf"^(?P<name>{_NAME})\[(?:/(?P<ip>[0-9a-fA-F.:]+?):(?P<port>\d+)|(?P<local>local))\] "
            r"logged in with entity id (?P<entity_id>\d+) at \((?P<pos>[^)]*)\)"
        ),
    ),
    ("player_leave", re.compile(rf"^(?P<name>{_NAME}) lost connection: (?P<reason>.*)$")),
    # 进服前被拒（白名单 / 封禁 / 已在线 …）：名字后带来源地址，没有对应的会话
    #   FishNet (/93.184.216.198:61000) lost connection: You are not white-listed on this server!
    (
        "login_rejected",
        re.compile(
            rf"^(?P<name>{_NAME}) \(/(?P<ip>[0-9a-fA-F.:]+?):(?P<port>\d+)\) "
            r"lost connection: (?P<reason>.*)$"
        ),
    ),
    (
        "player_uuid",
        re.compile(rf"^UUID of player (?P<name>{_NAME}) is (?P<uuid>[0-9a-f-]{{36}})$"),
    ),
    (
        "auth_attempt",
        re.compile(rf"^\[EasyAuth\]: \[LOGIN\]: Player (?P<name>{_NAME}) is trying to login$"),
    ),
    (
        "auth_success",
        re.compile(
            rf"^\[EasyAuth\]: \[LOGIN\]: Player (?P<name>{_NAME}) provide correct password$"
        ),
    ),
    (
        "auth_failure",
        re.compile(
            rf"^\[EasyAuth\]: \[LOGIN\]: Player (?P<name>{_NAME}) "
            r"(?:provide (?:wrong|incorrect) password|entered wrong password).*$"
        ),
    ),
    (
        # 实际格式：Player <name>{<uuid>} successfully registered with password: <bcrypt 哈希>
        # 线程是 pool-2-thread-N；哈希不入库
        "auth_register",
        re.compile(
            rf"^\[EasyAuth\]: \[REGISTER\]: Player (?P<name>{_NAME})"
            r"(?:\{(?P<uuid>[0-9a-f-]{36})\})? successfully registered\b"
        ),
    ),
    ("ban_ip", re.compile(r"^Banned IP (?P<ip>[0-9a-fA-F.:]+): (?P<reason>.*)$")),
    ("ban_player", re.compile(rf"^Banned (?P<name>{_NAME}): (?P<reason>.*)$")),
    (
        "anticheat",
        re.compile(
            rf"^(?P<name>{_NAME}) (?P<kind>moved too quickly|moved wrongly)!?(?P<detail>.*)$"
        ),
    ),
    # enforce-secure-profile=false 时聊天行带 "[Not Secure] " 前缀
    ("chat", re.compile(rf"^(?:\[Not Secure\] )?<(?P<name>{_NAME})> (?P<message>.*)$")),
    # carpet 客户端握手：真实玩家每次登录都有，10-04 攻击者的会话没有（客户端指纹）
    (
        "carpet_client",
        re.compile(rf"^Player (?P<name>{_NAME}) joined with a matching carpet client$"),
    ),
    ("server_ready", re.compile(r"^Done \((?P<seconds>[\d.]+)s\)! For help")),
    ("server_stopping", re.compile(r"^Stopping (?:the )?server$")),
]

# 被踢原因中的外挂特征（例如 10-04 攻击者开飞行被踢：Flying is not enabled on this server）
_ANTICHEAT_LEAVE_REASONS = ("Flying is not enabled", "floating too long", "moved too quickly")


@dataclass(slots=True, frozen=True)
class LogLine:
    ts: datetime
    thread: str
    level: str
    message: str
    raw: str


@dataclass(slots=True)
class ParsedEvent:
    type: str
    ts: datetime
    data: dict[str, Any] = field(default_factory=dict)


def parse_line(raw: str, day: date, tz: tzinfo) -> LogLine | None:
    m = _LINE_RE.match(raw.rstrip("\r\n"))
    if not m:
        return None
    hh, mm, ss, thread, level, msg = m.groups()
    ts = datetime.combine(day, time(int(hh), int(mm), int(ss)), tzinfo=tz)
    return LogLine(ts=ts, thread=thread, level=level, message=msg, raw=raw.rstrip("\r\n"))


def _typed(kind: str, d: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in d.items() if v is not None}
    if kind == "player_login":
        out["entity_id"] = int(out["entity_id"])
        if "port" in out:
            out["port"] = int(out["port"])
        # carpet 假人显示为 [local]，没有网络来源
        out["fake"] = out.pop("local", None) == "local"
        out.setdefault("ip", None)
        try:
            out["pos"] = [round(float(x), 2) for x in out["pos"].split(",")]
        except ValueError:
            pass
    elif kind == "login_rejected":
        out["port"] = int(out["port"])
    elif kind == "server_ready":
        out["seconds"] = float(out["seconds"])
    elif kind == "anticheat":
        out["detail"] = out.get("detail", "").strip()
    return out


def classify(line: LogLine) -> list[ParsedEvent]:
    """一行日志可能产生 0..n 个事件（例如因飞行被踢 = player_leave + anticheat）。"""
    msg = _COLOR_RE.sub(
        "", line.message
    )  # 去掉 § 颜色码（如 "§cAuthentication time has expired."）
    for kind, pat in _PATTERNS:
        m = pat.match(msg)
        if not m:
            continue
        data = _typed(kind, m.groupdict())
        events = [ParsedEvent(kind, line.ts, data)]
        if kind == "player_leave" and any(r in data["reason"] for r in _ANTICHEAT_LEAVE_REASONS):
            events.append(
                ParsedEvent(
                    "anticheat",
                    line.ts,
                    {"name": data["name"], "kind": "kicked", "detail": data["reason"]},
                )
            )
        return events
    return []


class DayTracker:
    """为没有日期的日志行推断日期：时间倒退（跨过午夜）即日期 +1。"""

    def __init__(self, start: date) -> None:
        self.day = start
        self._last: time | None = None

    def feed(self, raw: str) -> date:
        m = _LINE_RE.match(raw)
        if m:
            t = time(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if (
                self._last is not None
                and t < self._last
                and (
                    datetime.combine(date.min, self._last) - datetime.combine(date.min, t)
                    > timedelta(hours=1)
                )
            ):
                self.day += timedelta(days=1)
            self._last = t
        return self.day
