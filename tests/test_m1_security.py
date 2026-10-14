"""M1：会话追踪 + 安全规则，用合成日志与（可选）生产日志回放验证。"""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops.db import open_db
from mcops.edge import parse_journal_line
from mcops.events import Event
from mcops.pipeline import Pipeline, backfill
from mcops.players import PlayerTracker, ServerFiles, ip_trusted, list_players, player_detail
from mcops.security import SecurityConfig, SecurityEngine, ack_alerts, list_alerts

CST = timezone(timedelta(hours=8))
L = "[{t}] [Server thread/INFO]: {m}"


def login(
    t: str, name: str, ip: str = "203.0.113.10", port: int = 50000, local: bool = False
) -> str:
    src = "local" if local else f"/{ip}:{port}"
    return L.format(t=t, m=f"{name}[{src}] logged in with entity id 1 at (0.0, 64.0, 0.0)")


def carpet(t: str, name: str) -> str:
    return L.format(t=t, m=f"Player {name} joined with a matching carpet client")


def auth_ok(t: str, name: str) -> str:
    return L.format(t=t, m=f"[EasyAuth]: [LOGIN]: Player {name} provide correct password")


def leave(t: str, name: str, why: str = "Disconnected") -> str:
    return L.format(t=t, m=f"{name} lost connection: {why}")


class Env:
    def __init__(self, engine: AsyncEngine, pipe: Pipeline, sent: list[dict[str, Any]]) -> None:
        self.engine, self.pipe, self.sent = engine, pipe, sent


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    engine = await open_db(tmp_path / "t.db")
    sent: list[dict[str, Any]] = []

    async def notifier(a: dict[str, Any]) -> None:
        sent.append(a)

    sec = SecurityEngine(engine, CST, SecurityConfig(), notifier=notifier)
    yield Env(engine, Pipeline(PlayerTracker(engine), sec), sent)
    await engine.dispose()


def write_log(tmp: Path, day: str, lines: list[str], idx: int = 1) -> Path:
    p = tmp / f"{day}-{idx}.log.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return p


async def feed(env: Env, tmp: Path, day: str, lines: list[str], idx: int = 1) -> None:
    await backfill(env.engine, env.pipe, [write_log(tmp, day, lines, idx)], CST)


def rules_fired(env: Env) -> Counter[str]:
    return Counter(a["rule"] for a in env.sent)


def test_ip_trust() -> None:
    assert not ip_trusted("127.0.0.1") and not ip_trusted(None) and not ip_trusted("::1")
    assert ip_trusted("203.0.113.5") and ip_trusted("2001:db8::1")


async def test_sessions_are_built_and_closed(env: Env, tmp_path: Path) -> None:
    await feed(
        env,
        tmp_path,
        "2026-10-10",
        [
            login("12:00:00", "Alice"),
            carpet("12:00:01", "Alice"),
            auth_ok("12:00:05", "Alice"),
            login("12:01:00", "Bot1", local=True),
            leave("12:30:00", "Alice"),
            login("13:00:00", "Bob", ip="127.0.0.1"),
            # 文件结束 → Bob 的会话被视为随停服关闭
        ],
    )
    sessions, ips = await player_detail(env.engine, "alice")
    (s,) = sessions
    assert s["ip"] == "203.0.113.10" and s["ip_trusted"]
    assert s["authenticated"] is True and s["carpet_client"] is True
    # 没有任何 EasyAuth 认证日志的会话：认证状态未知，而不是失败
    (bob0,) = (await player_detail(env.engine, "Bob"))[0]
    assert bob0["authenticated"] is None
    assert s["logout_at"].startswith("2026-10-10T04:30:00")
    assert ips[0]["trusted"] is True
    (bob,) = (await player_detail(env.engine, "Bob"))[0]
    assert bob["ip_trusted"] is False and bob["leave_reason"] == "<server_stopped>"
    # 回填产生的告警只用于复盘，不发通知
    assert env.sent == []


async def test_live_rules_new_account_night_and_fingerprint(env: Env) -> None:
    sec = env.pipe.security
    sec.historical = False

    async def ev(type_: str, hhmmss: str, **data: Any) -> None:
        h, m, s = map(int, hhmmss.split(":"))
        ts = datetime(2026, 10, 11, h, m, s, tzinfo=CST).astimezone(UTC)
        await env.pipe.process(Event(type_, "mc_log", data, ts=ts))
        await sec.tick(ts)

    # 建立历史：Yuki_Y 一贯用 carpet，从 203.0.113.0/24 登录
    for i, hh in enumerate(["13", "14", "15", "16", "17"]):
        await ev(
            "player_login",
            f"{hh}:00:00",
            name="Yuki_Y",
            ip="203.0.113.7",
            port=1000 + i,
            entity_id=1,
            fake=False,
        )
        await ev("carpet_client", f"{hh}:00:01", name="Yuki_Y")
        await ev("auth_success", f"{hh}:00:02", name="Yuki_Y")
        await ev("player_leave", f"{hh}:30:00", name="Yuki_Y", reason="Disconnected")
    env.sent.clear()

    # 02:47 冒用者：新网段、凌晨、没有 carpet 握手、开飞行被踢
    await ev(
        "player_login",
        "02:47:39",
        name="Yuki_Y",
        ip="198.51.100.96",
        port=33583,
        entity_id=2,
        fake=False,
    )
    await ev("auth_success", "02:47:45", name="Yuki_Y")
    await ev(
        "anticheat",
        "02:48:10",
        name="Yuki_Y",
        kind="kicked",
        detail="Flying is not enabled on this server",
    )
    fired = rules_fired(env)
    assert fired["new_ip"] == 1
    assert fired["night_login"] == 1
    assert fired["fingerprint_mismatch"] == 1
    assert fired["anticheat_kick"] == 1
    night = next(a for a in env.sent if a["rule"] == "night_login")
    assert night["severity"] == "critical"  # 凌晨 + 新网段 → 升级
    fp = next(a for a in env.sent if a["rule"] == "fingerprint_mismatch")
    assert fp["severity"] == "critical" and "疑似冒用" in fp["title"]
    kick0 = next(a for a in env.sent if a["rule"] == "anticheat_kick")
    assert kick0["severity"] == "critical"  # 凌晨被踢 → 升级

    # 同一问题在冷却期内不重复通知
    await ev(
        "anticheat",
        "02:49:00",
        name="Yuki_Y",
        kind="kicked",
        detail="Flying is not enabled on this server",
    )
    assert rules_fired(env)["anticheat_kick"] == 1
    items, counts = await list_alerts(env.engine)
    kick = next(a for a in items if a["rule"] == "anticheat_kick")
    assert kick["count"] == 2
    assert counts["critical"] >= 3
    assert await ack_alerts(env.engine, alert_id=kick["id"]) == 1


async def test_daytime_single_kick_is_warning(env: Env) -> None:
    ts = datetime(2026, 10, 9, 14, 47, 26, tzinfo=CST).astimezone(UTC)
    d = {
        "name": "Carol",
        "kind": "kicked",
        "detail": "Flying is not enabled on this server",
    }
    await env.pipe.process(Event("anticheat", "mc_log", d, ts=ts))
    kick = next(a for a in env.sent if a["rule"] == "anticheat_kick")
    assert kick["severity"] == "warning" and "卡顿误判" in kick["detail"]


async def test_multi_account_and_auth_burst(env: Env) -> None:
    sec = env.pipe.security
    base = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    for i, n in enumerate(["a1", "a2", "a3"]):
        await env.pipe.process(
            Event(
                "player_login",
                "mc_log",
                {"name": n, "ip": "198.51.100.9", "port": i, "entity_id": i, "fake": False},
                ts=base + timedelta(minutes=i),
            )
        )
    assert rules_fired(env)["ip_multi_account"] == 1
    # 三次登录未认证就离开 → 疑似爆破
    for i in range(3):
        t = base + timedelta(minutes=10 + i)
        await env.pipe.process(
            Event(
                "player_login",
                "mc_log",
                {"name": "victim", "ip": "198.51.100.50", "port": i, "entity_id": i, "fake": False},
                ts=t,
            )
        )
        await env.pipe.process(
            Event(
                "player_leave",
                "mc_log",
                {"name": "victim", "reason": "Authentication time has expired."},
                ts=t + timedelta(seconds=60),
            )
        )
    assert rules_fired(env)["auth_failure_burst"] == 1
    assert sec is env.pipe.security


async def test_frps_ssh_burst_respects_allowlist(env: Env) -> None:
    sec = env.pipe.security
    sec.cfg.ip_allowlist = {"203.0.113.1"}
    t0 = datetime(2026, 10, 11, tzinfo=UTC)
    for i in range(25):
        for ip in ("198.51.100.1", "203.0.113.1"):
            await env.pipe.process(
                Event(
                    "frps_conn",
                    "edge",
                    {"proxy": "ssh", "ip": ip, "port": i},
                    ts=t0 + timedelta(seconds=i),
                )
            )
    fired = [a for a in env.sent if a["rule"] == "frps_ssh_burst"]
    assert [a["subject"] for a in fired] == ["198.51.100.1"]


async def test_unregistered_whitelisted(env: Env, tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "EasyAuth").mkdir(parents=True)
    (game / "whitelist.json").write_text(
        json.dumps([{"uuid": "x", "name": "Alice"}, {"uuid": "y", "name": "Zoe"}])
    )
    (game / "ops.json").write_text(json.dumps([{"uuid": "x", "name": "Alice"}]))
    with sqlite3.connect(game / "EasyAuth" / "easyauth.db") as c:
        c.execute(
            "create table easyauth (id integer, username text, username_lower text, uuid text, data text, last_ip text)"
        )
        c.execute(
            "insert into easyauth values (1,'Alice','alice','x',?,'')",
            (json.dumps({"password": "hash"}),),
        )
    files = ServerFiles(game, game / "EasyAuth" / "easyauth.db")
    assert files.easyauth_registered() == {"alice"}
    now = datetime.now(UTC)
    await env.pipe.security.check_unregistered(files.whitelist(), files.easyauth_registered(), now)
    await env.pipe.security.check_unregistered(
        files.whitelist(), files.easyauth_registered(), now + timedelta(minutes=10)
    )
    assert [a["subject"] for a in env.sent] == ["Zoe"]  # 只通知一次
    players = await list_players(env.engine, files)
    assert players[0]["name"] == "Zoe" and players[0]["flags"] == ["unregistered_whitelisted"]
    alice = next(p for p in players if p["name"] == "Alice")
    assert alice["is_op"] and alice["easyauth_registered"]


def test_parse_frps_journal() -> None:
    def j(msg: str) -> str:
        return json.dumps(
            {"__CURSOR": "s=abc;i=1", "__REALTIME_TIMESTAMP": "1791102457000000", "MESSAGE": msg}
        )

    cur, rec = parse_journal_line(
        j(
            "2026-10-04 02:47:37.548 [I] [proxy/proxy.go:259] [a41fcbfb1dcffca8] [minecraft] get a user connection [198.51.100.96:33583]"
        )
    )
    assert cur == "s=abc;i=1"
    assert rec is not None and rec.kind == "conn"
    assert rec.data == {"proxy": "minecraft", "ip": "198.51.100.96", "port": 33583}
    _, rec = parse_journal_line(
        j(
            "[W] [server/service.go:471] register control error: token in login doesn't match token from configuration"
        )
    )
    assert rec is not None and rec.kind == "auth_failed"
    _, rec = parse_journal_line(
        j(
            "client login info: ip [198.51.100.72:9718] version [0.67.0] hostname [censys.local] os [linux] arch []"
        )
    )
    assert rec is not None and rec.kind == "client_login" and rec.data["hostname"] == "censys.local"
    assert parse_journal_line("not json") == (None, None)


REPLAY = os.environ.get("MCOPS_REPLAY_DIR")


@pytest.mark.skipif(not REPLAY, reason="未设置 MCOPS_REPLAY_DIR")
async def test_backfill_flags_20261004_incident(env: Env) -> None:
    paths = sorted(Path(REPLAY or "").glob("2026-10-0*.log.gz"))
    await backfill(env.engine, env.pipe, paths, CST)
    items, _ = await list_alerts(env.engine, status="all", limit=1000)
    rules = Counter(a["rule"] for a in items if a["subject"] == "Yuki_Y")
    # 10-04 的 IP 都是 127.0.0.1（不可信），无法判断新网段；但凌晨登录、被踢、移动异常必须能抓到
    assert rules["night_login"] >= 1
    assert rules["anticheat_kick"] >= 1
    assert rules["anticheat_burst"] >= 1
    assert all(a["status"] == "acked" for a in items)  # 回填告警只用于复盘


def test_parse_frps_journal_ansi_byte_array() -> None:
    # frps 日志带 ANSI 颜色码，journald 以字节数组存储 MESSAGE（生产实测）
    msg = "\x1b[0m\x1b[1;34m2026-10-09 11:29:17.728 [I] [proxy/proxy.go:259] [1115fbecccadf54f] [ssh] get a user connection [198.51.100.7:51234]\x1b[0m"
    raw = json.dumps(
        {
            "__CURSOR": "s=1",
            "__REALTIME_TIMESTAMP": "1791566957000000",
            "MESSAGE": list(msg.encode()),
        }
    )
    _, rec = parse_journal_line(raw)
    assert rec is not None and rec.data == {"proxy": "ssh", "ip": "198.51.100.7", "port": 51234}


async def test_fast_move_elytra_aware(env: Env) -> None:
    """moved too quickly：鞘翅飞行中只看频率，非鞘翅每次都告警，无法判断时沿用旧阈值。"""
    sec = env.pipe.security
    sec.historical = False
    state: dict[str, bool | None] = {"Frank528": True, "Speedy": False, "Ghost": None}

    async def probe(name: str) -> bool | None:
        return state[name]

    sec.elytra_probe = probe

    async def move(name: str, t: datetime) -> None:
        await env.pipe.process(
            Event(
                "anticheat",
                "mc_log",
                {"name": name, "kind": "moved too quickly", "detail": " 18.8,-1.2,3.5"},
                ts=t,
            )
        )

    base = datetime(2026, 10, 10, 18, 49, tzinfo=CST).astimezone(UTC)
    # 生产实测的正常鞘翅飞行：约每分钟 1 次，5 分钟最多 8 次 → 不告警
    for i in range(20):
        await move("Frank528", base + timedelta(seconds=40 * i))
    assert rules_fired(env)["anticheat_burst"] == 0

    # 鞘翅飞行但 5 分钟内 15 次 → 告警一次
    burst = base + timedelta(hours=2)
    for i in range(16):
        await move("Frank528", burst + timedelta(seconds=15 * i))
    alerts = [a for a in env.sent if a["rule"] == "anticheat_burst"]
    assert len(alerts) == 1 and alerts[0]["data"]["elytra"] is True

    # 不在鞘翅飞行：单次就告警（warning），带位移距离
    await move("Speedy", base)
    (a,) = [a for a in env.sent if a["rule"] == "speed_no_elytra"]
    assert a["severity"] == "warning" and a["data"]["distance"] == 19.2

    # 凌晨的非鞘翅超速 → critical（新 subject 才会新建告警）
    night = datetime(2026, 10, 11, 3, 0, tzinfo=CST).astimezone(UTC)
    state["NightOwl"] = False
    await move("NightOwl", night)
    (n,) = [a for a in env.sent if a["subject"] == "NightOwl"]
    assert n["severity"] == "critical"

    # 无法判断（RCON 不可用）：沿用 2 分钟 ≥5 次
    for i in range(4):
        await move("Ghost", base + timedelta(seconds=10 * i))
    assert not [a for a in env.sent if a["subject"] == "Ghost"]
    await move("Ghost", base + timedelta(seconds=50))
    (g,) = [a for a in env.sent if a["subject"] == "Ghost"]
    assert g["rule"] == "anticheat_burst" and g["data"]["elytra"] is None


async def test_fast_move_fall_is_ignored(env: Env) -> None:
    """向下、水平几乎不动的超速是下落：不告警、不查 RCON；水平超速和向上照常告警。"""
    sec = env.pipe.security
    sec.historical = False
    probed: list[str] = []

    async def probe(name: str) -> bool | None:
        probed.append(name)
        return False

    sec.elytra_probe = probe

    async def move(name: str, detail: str) -> None:
        await env.pipe.process(
            Event(
                "anticheat",
                "mc_log",
                {"name": name, "kind": "moved too quickly", "detail": detail},
                ts=datetime(2026, 10, 11, 18, 34, tzinfo=CST).astimezone(UTC),
            )
        )

    # 生产 10-11 18:34 的真实样本：几乎纯垂直下落 14 格
    await move("Faller", " 0.15490507120125585,-13.9424172396292,0.24819048565859703")
    # 下落带一点水平位移（≤4 格）也算下落
    await move("Faller", " 3.0,-20.0,2.0")
    assert not [a for a in env.sent if a["subject"] == "Faller"] and probed == []

    # 生产 10-10 的样本：水平 17 格 → 照常告警
    await move("Runner", " -16.793703085305808,-0.6931144566432295,5.751632272001153")
    # 向上弹射（不是下落）→ 照常告警
    await move("Jumper", " 0.2,14.0,0.1")
    fired = {a["subject"] for a in env.sent if a["rule"] == "speed_no_elytra"}
    assert fired == {"Runner", "Jumper"}


async def test_fast_move_threshold_is_configurable(env: Env) -> None:
    sec = env.pipe.security
    sec.historical = False
    sec.cfg.elytra_burst_count = 3
    sec.cfg.elytra_burst_window_minutes = 1

    async def probe(name: str) -> bool | None:
        return True

    sec.elytra_probe = probe
    t = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    for i in range(3):
        await env.pipe.process(
            Event(
                "anticheat",
                "mc_log",
                {"name": "Alice", "kind": "moved too quickly", "detail": " 1,1,1"},
                ts=t + timedelta(seconds=i),
            )
        )
    assert rules_fired(env)["anticheat_burst"] == 1


async def test_online_roster_splits_fake_players(env: Env, tmp_path: Path) -> None:
    from mcops.players import online_roster

    sec = env.pipe.security
    sec.historical = False
    t = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    for i, (name, local) in enumerate([("Zoe", True), ("Alice", False), ("Sandy", True)]):
        await env.pipe.process(
            Event(
                "player_login",
                "mc_log",
                {
                    "name": name,
                    "ip": None if local else "203.0.113.9",
                    "port": None if local else 1,
                    "entity_id": i,
                    "fake": local,
                },
                ts=t + timedelta(seconds=i),
            )
        )
    assert await online_roster(env.engine) == (["Alice"], ["Zoe", "Sandy"])

    # 状态快照：样本里的 Anonymous Player 换成真实名单，并分出假人
    from mcops import slp
    from mcops.backend import ObserveBackend
    from mcops.config import ServerConfig
    from mcops.events import EventBus
    from mcops.rcon import RconClient
    from mcops.server import ServerController, State

    cfg = ServerConfig(game_dir=tmp_path, jar="server.jar")
    srv = ServerController(
        cfg, ObserveBackend(cfg), RconClient("127.0.0.1", 1, ""), EventBus(env.engine), CST
    )
    srv.state = State.RUNNING
    srv._last_players = slp.PingResult(
        online=3, max=25, names=["Anonymous Player", "Alice", "Anonymous Player"], version=None
    )
    srv._roster = await online_roster(env.engine)
    p = srv.status_snapshot()["players"]
    assert p["real"] == ["Alice"] and p["fake"] == ["Zoe", "Sandy"]
    assert p["names"] == ["Alice", "Zoe", "Sandy"]
    assert (p["real_count"], p["fake_count"]) == (1, 2)
