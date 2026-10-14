"""M4：悬空/飞行检测（只告警）。真实服务端上的端到端验证见 dev 脚本；这里测判定逻辑。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from mcops.flight import FlightWatch, is_fall_flying, parse_online, probe_cmd

CST = timezone(timedelta(hours=8))


async def test_is_fall_flying() -> None:
    replies = {
        "data get entity Frank528 FallFlying": "Frank528 has the following entity data: 1b",
        "data get entity Alice FallFlying": "Alice has the following entity data: 0b",
        "data get entity Gone FallFlying": "No entity was found",
    }
    sent: list[str] = []

    async def cmd(c: str) -> str:
        sent.append(c)
        if c not in replies:
            raise ConnectionError("rcon down")
        return replies[c]

    assert await is_fall_flying(cmd, "Frank528") is True
    assert await is_fall_flying(cmd, "Alice") is False
    assert await is_fall_flying(cmd, "Gone") is None
    assert await is_fall_flying(cmd, "Bob") is None  # RCON 异常
    n = len(sent)
    assert await is_fall_flying(cmd, "a b; stop") is None and len(sent) == n  # 非法名字不发命令


class FakeServer:
    def __init__(self) -> None:
        self.online: list[str] = []
        self.pos: dict[
            str, tuple[float, float, float] | None
        ] = {}  # None = 脚下有方块（probe 无输出）

    async def command(self, cmd: str) -> str:
        if cmd == "list":
            return f"There are {len(self.online)} of a max of 25 players online: " + ", ".join(
                self.online
            )
        name = cmd.split()[2]
        p = self.pos.get(name)
        return (
            ""
            if p is None
            else f"{name} has the following entity data: [{p[0]}d, {p[1]}d, {p[2]}d]"
        )


def make(fs: FakeServer, alerts: list[dict[str, Any]]) -> FlightWatch:
    async def raise_alert(
        rule: str,
        ts: datetime,
        title: str,
        detail: str,
        subject: str,
        data: dict[str, Any],
        severity: str | None = None,
    ) -> None:
        alerts.append({"rule": rule, "title": title, "severity": severity, **data})

    return FlightWatch(fs.command, raise_alert, CST, lambda: True, interval_s=3, samples=5)


def test_parse_online_and_probe() -> None:
    assert parse_online("There are 2 of a max of 25 players online: Yuki_Y, Bot1") == [
        "Yuki_Y",
        "Bot1",
    ]
    assert parse_online("There are 0 of a max of 25 players online: ") == []
    cmd = probe_cmd("Yuki_Y")
    assert cmd.startswith("execute as Yuki_Y at @s") and cmd.count("#minecraft:air") == 8
    assert "OnGround" not in cmd  # 客户端可伪造，不能信


async def test_hover_alerts_once_falling_does_not() -> None:
    fs, alerts = FakeServer(), []  # type: ignore[var-annotated]
    fw = make(fs, alerts)
    fs.online = ["Hacker", "Faller", "Walker"]
    t0 = datetime(2026, 10, 9, 15, 0, tzinfo=CST).astimezone(UTC)
    for i in range(8):
        fs.pos["Hacker"] = (10.0, 120.0 + (i % 2) * 0.3, 5.0)  # 悬停/微动
        fs.pos["Faller"] = (0.0, 200.0 - i * 20, 0.0)  # 下落
        fs.pos["Walker"] = None
        await fw.tick(t0 + timedelta(seconds=3 * i))
    assert [a["name"] for a in alerts] == ["Hacker"]  # 只告警一次
    assert (
        alerts[0]["rule"] == "flight_suspect"
        and alerts[0]["severity"] is None
        and alerts[0]["y"] == 120.0
    )


async def test_landing_resets_and_night_escalates() -> None:
    fs, alerts = FakeServer(), []  # type: ignore[var-annotated]
    fw = make(fs, alerts)
    fs.online = ["P"]
    t0 = datetime(2026, 10, 10, 2, 30, tzinfo=CST).astimezone(UTC)
    for i in range(4):  # 悬空 4 次后落地：不告警
        fs.pos["P"] = (0.0, 80.0, 0.0)
        await fw.tick(t0 + timedelta(seconds=3 * i))
    fs.pos["P"] = None
    await fw.tick(t0 + timedelta(seconds=12))
    for i in range(5):
        fs.pos["P"] = (0.0, 80.0, 0.0)
        await fw.tick(t0 + timedelta(seconds=15 + 3 * i))
    assert len(alerts) == 1 and alerts[0]["severity"] == "critical"  # 凌晨升级
    fs.online = []  # 下线清理状态
    await fw.tick(t0 + timedelta(seconds=40))
    assert fw._tracks == {}
