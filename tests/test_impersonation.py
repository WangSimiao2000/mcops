"""冒用玩家名尝试进服（10-13 02:31 扫描器 93.184.216.198 冒用在线玩家 Carol）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select

from mcops import db, logparse
from mcops.db import open_db
from mcops.pipeline import Pipeline
from mcops.players import PlayerTracker
from mcops.security import SecurityConfig, SecurityEngine
from tests.test_m1_security import CST, Env, L, feed, login

SCANNER = "93.184.216.198"


def rejected(t: str, name: str, ip: str, why: str) -> str:
    return L.format(t=t, m=f"{name} (/{ip}:54886) lost connection: §c{why}")


def test_parse_rejected_line() -> None:
    line = logparse.parse_line(
        rejected("02:31:58", "Carol", SCANNER, "Player Carol is already online!"),
        date(2026, 10, 13),
        CST,
    )
    assert line is not None
    (ev,) = logparse.classify(line)
    assert ev.type == "login_rejected"
    assert ev.data == {
        "name": "Carol",
        "ip": SCANNER,
        "port": 54886,
        "reason": "Player Carol is already online!",
    }
    # 普通离线行仍是 player_leave
    plain = logparse.parse_line(
        L.format(t="05:04:05", m="Carol lost connection: Disconnected"),
        date(2026, 10, 13),
        CST,
    )
    assert plain is not None
    assert [e.type for e in logparse.classify(plain)] == ["player_leave"]


async def _env(tmp_path: Path, banned: set[str]) -> Env:
    engine = await open_db(tmp_path / "t.db")
    sent: list[dict[str, Any]] = []

    async def notifier(a: dict[str, Any]) -> None:
        sent.append(a)

    sec = SecurityEngine(
        engine, CST, SecurityConfig(), notifier=notifier, banned_ips=lambda: banned
    )
    return Env(engine, Pipeline(PlayerTracker(engine), sec), sent)


async def _alerts(env: Env) -> list[dict[str, Any]]:
    async with env.engine.connect() as conn:
        rows = await conn.execute(
            select(db.alerts).where(db.alerts.c.rule == "impersonation_attempt")
        )
        return [dict(r) for r in rows.mappings()]


async def test_banned_ip_using_known_name_is_critical(tmp_path: Path) -> None:
    env = await _env(tmp_path, {SCANNER})
    try:
        await feed(
            env,
            tmp_path,
            "2026-10-13",
            [
                login("00:00:00", "Carol", ip="203.0.113.10"),
                rejected(
                    "02:31:58",
                    "Carol",
                    SCANNER,
                    "Player Carol is already online!",
                ),
                # 扫描器平时用的名字从没进过服：白名单已经挡住，不告警
                rejected(
                    "02:58:52", "FishNet", SCANNER, "You are not white-listed on this server!"
                ),
            ],
        )
        alerts = await _alerts(env)
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critical"
        assert alerts[0]["subject"] == "Carol"
        assert SCANNER in alerts[0]["title"]
    finally:
        await env.engine.dispose()


async def test_unknown_prefix_warns_but_own_reconnect_does_not(tmp_path: Path) -> None:
    env = await _env(tmp_path, set())
    try:
        await feed(
            env,
            tmp_path,
            "2026-10-13",
            [
                login("10:00:00", "Alice", ip="203.0.113.10"),
                # 本人掉线后马上重连（同 /24）：已在线被拒，不告警
                rejected("10:05:00", "Alice", "203.0.113.77", "Player Alice is already online!"),
                # 陌生网段用 Alice 的名字：警告
                rejected("10:06:00", "Alice", "198.51.100.9", "Player Alice is already online!"),
            ],
        )
        alerts = await _alerts(env)
        assert [a["severity"] for a in alerts] == ["warning"]
        assert "198.51.100.9" in alerts[0]["title"]
    finally:
        await env.engine.dispose()
