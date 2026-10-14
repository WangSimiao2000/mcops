"""M2：QQ 机器人。移植旧 mc-qq-bot 的 test_whitelist_flow.py / test_exposure.py 断言，
并补上旧实现没有测试的监控 / 维护状态机。"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert

from mcops import db
from mcops.bot import texts as T
from mcops.bot.core import BotCore, BotSettings, MCStatus, Message
from mcops.bot.migrate import import_legacy
from mcops.bot.whitelist import (
    WhitelistError,
    WhitelistFile,
    offline_uuid,
    parse_whitelist_list,
    validate_name,
)
from mcops.db import open_db

ADMIN = "ADMIN_OPENID_FAKE"
STRANGER = "STRANGER_OPENID_FAKE"
GROUP = "GROUP_OPENID_FAKE"

FORBIDDEN = [
    r"config\.yaml",
    r"whitelist\.json",
    r"mc\.sh",
    r"applications\.json",
    r"members\.json",
    r"/home/",
    r"MC_Server",
    r"admin_openids",
    r"管理脚本",
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",  # UUID
]


def leaks(text: str) -> list[str]:
    return [p for p in FORBIDDEN if re.search(p, text or "", re.I)]


class FakeChannel:
    """模拟服务端：记录命令；`whitelist list` 返回当前文件内容（与真实服务端一致）。"""

    def __init__(self, wl: WhitelistFile, broken: bool = False) -> None:
        self.wl, self.broken, self.commands = wl, broken, []  # type: ignore[var-annotated]

    async def run(self, command: str) -> str | None:
        self.commands.append(command)
        if self.broken:
            return None
        if command == "whitelist list":
            names = self.wl.names()
            return (
                f"There are {len(names)} whitelisted player(s): {', '.join(names)}"
                if names
                else "There are no whitelisted players"
            )
        return ""


class Pushed(list[tuple[str, set[str]]]):
    async def __call__(self, content: str, groups: set[str]) -> int:
        self.append((content, groups))
        return len(groups)


@pytest.fixture
async def bot(tmp_path: Path) -> AsyncIterator[BotCore]:
    engine = await open_db(tmp_path / "t.db")
    game = tmp_path / "game"
    (game / "mods").mkdir(parents=True)
    for j in (
        "fabric-api-0.141.3+1.21.11.jar",
        "lithium-fabric-0.21.4+mc1.21.11.jar",
        "easyauth-mc1.21.11-3.4.3.jar",
    ):
        (game / "mods" / j).write_bytes(b"")
    wl = WhitelistFile(game / "whitelist.json")
    wl.add("OpAdmin")
    s = BotSettings(
        display_host="mc.example.test",
        query_port=1,
        admin_openids={ADMIN},
        allowed_groups={GROUP},
        max_pending_per_user=2,
        max_pending_total=3,
        fail_threshold=3,
        watchdog_state_file=tmp_path / "state",
        maintenance_signal_file=tmp_path / "maint.signal",
        maintenance_cooldown=300,
    )

    async def no_pid() -> int | None:
        return None

    core = BotCore(engine, s, wl, FakeChannel(wl), game, no_pid)
    core.pusher = Pushed()
    yield core
    await engine.dispose()


def M(openid: str | None, content: str = "x", group: str = GROUP) -> Message:
    return Message(content=content, group_openid=group, actor_openid=openid)


# ---- 1. 严格指令解析（test_whitelist_flow.py §1） -------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("白名单 列表", ("wl_list", None)),
        ("白名单列表", ("wl_list", None)),
        ("wl list", ("wl_list", None)),
        ("白名单 添加 ZzTestCase9", ("wl_add", "ZzTestCase9")),
        ("添加白名单 ZzTestCase9", ("wl_add", "ZzTestCase9")),
        ("白名单 移除 ZzTestCase9", ("wl_remove", "ZzTestCase9")),
        ("申请白名单 ZzTestCase9", ("apply", "ZzTestCase9")),
        ("待审", ("pending", None)),
        ("批准 ZzTestCase9", ("approve", "ZzTestCase9")),
        ("拒绝 ZzTestCase9", ("reject", "ZzTestCase9")),
        ("我的id", ("myid", None)),
        ("<@!12345> 申请白名单 ZzTestCase9", ("apply", "ZzTestCase9")),
        ("/白名单 添加 ZzTestCase9", ("wl_add", "ZzTestCase9")),
        ("状态", None),
        ("在线玩家", None),
        ("列表", None),
        ("帮助", None),
        ("我想申请白名单", None),
        ("批准", None),
    ],
)
def test_strict_parse(raw: str, want: tuple[str, str | None] | None) -> None:
    assert T.parse_strict_command(raw) == want


# ---- 2. 离线 UUID 与白名单文件 -------------------------------------------------


def test_offline_uuid_matches_server() -> None:
    assert offline_uuid("ZzNeverSeen42") == "32869268-9827-3c46-a566-114043a91d59"
    assert offline_uuid("zzneverseen42") == "402cc456-bedc-3ec6-8e41-72a65ed40203"
    assert offline_uuid("Yuki_Y") == "d3496a7e-5b41-3bb8-9654-b553200cc07c"


def test_name_validation_and_case_conflict(tmp_path: Path) -> None:
    for bad in ["ab", "a" * 17, "a-b", "a b", "名字", "a;ls", "", None]:
        assert not validate_name(bad)
    wl = WhitelistFile(tmp_path / "whitelist.json")
    assert wl.add("OpAdmin") == offline_uuid("OpAdmin")
    with pytest.raises(WhitelistError, match="已在白名单"):
        wl.add("OpAdmin")
    with pytest.raises(WhitelistError, match="大小写不同"):
        wl.add("opadmin")
    assert wl.remove("OPADMIN") == "OpAdmin" and wl.names() == []
    assert parse_whitelist_list("There are 2 whitelisted player(s): A_one, B_two") == {
        "A_one",
        "B_two",
    }
    assert parse_whitelist_list("There are no whitelisted players") == set()
    assert parse_whitelist_list("无关") is None


# ---- 3. 授权（fail closed） ---------------------------------------------------


async def test_authorization(bot: BotCore) -> None:
    assert bot.is_admin(M(ADMIN))
    assert not bot.is_admin(M(STRANGER)) and not bot.is_admin(M(None)) and not bot.is_admin(M(""))
    assert not bot.is_admin(M(ADMIN, group="OTHER_GROUP"))
    bot.s.admin_openids = set()
    assert not bot.is_admin(M(ADMIN))
    r = await bot.handle_whitelist_command(M(ADMIN), "wl_add", "Someone")
    assert r.startswith("⛔") and "尚未完成管理员配置" in r


# ---- 4. 信息暴露面（test_exposure.py） -------------------------------------------


async def test_exposure_surface(bot: BotCore) -> None:
    st = MCStatus(online=True, version="1.21.11", players_max=25)
    join = bot.cmd_join(st)
    assert "mc.example.test" in join and "白名单" not in join and not leaks(join)

    for cmd in ["wl_add", "wl_remove", "wl_list", "pending", "approve", "reject"]:
        r = await bot.handle_whitelist_command(M(STRANGER), cmd, "SomeName")
        assert r.startswith("⛔") and not leaks(r), cmd

    r = await bot.handle_whitelist_command(M(STRANGER), "myid", None)
    assert r == T.MSG_ADMIN_ONLY and STRANGER not in r
    r = await bot.handle_whitelist_command(M(ADMIN), "myid", None)
    assert ADMIN in r and GROUP not in r

    _, perf = await bot.handle_message(M(STRANGER, "性能"))
    assert perf == T.MSG_ADMIN_ONLY
    _, perf = await bot.handle_message(M(ADMIN, "性能"))
    assert "磁盘" in perf

    hu, ha = T.help_text(False), T.help_text(True)
    assert "性能" not in hu and "我的id" not in hu and "管理员指令" not in hu and "我的白名单" in hu
    assert "性能" in ha and "全群可见" in ha
    assert not leaks(hu) and not leaks(ha)


async def test_apply_rate_limits_and_self_query(bot: BotCore) -> None:
    r1 = await bot.handle_whitelist_command(M(STRANGER), "apply", "AaaOne")
    r2 = await bot.handle_whitelist_command(M(STRANGER), "apply", "AaaTwo")
    r3 = await bot.handle_whitelist_command(M(STRANGER), "apply", "AaaThree")
    assert "已登记" in r1 and "已登记" in r2 and "待审申请" in r3 and "已登记" not in r3
    assert "已登记" in await bot.handle_whitelist_command(M("OTHER_ONE"), "apply", "BbbOne")
    assert "申请较多" in await bot.handle_whitelist_command(M("OTHER_TWO"), "apply", "CccOne")
    bad = await bot.handle_whitelist_command(M(STRANGER), "apply", "<script>x</script>")
    assert bad.startswith("❌") and "script" not in bad
    mine = await bot.handle_whitelist_command(M(STRANGER), "my_wl", None)
    assert "AaaOne" in mine and "BbbOne" not in mine and "OpAdmin" not in mine


# ---- 5. 申请 → 批准 → 服务端回读确认 ---------------------------------------------


async def test_apply_approve_verify_flow(bot: BotCore) -> None:
    await bot.handle_whitelist_command(M(STRANGER), "apply", "ZzTestCase9")
    pend = await bot.handle_whitelist_command(M(ADMIN), "pending", None)
    assert "ZzTestCase9" in pend
    r = await bot.handle_whitelist_command(
        M(ADMIN), "approve", "zztestcase9"
    )  # 大小写不敏感地找到申请
    assert r.startswith("✅") and "ZzTestCase9" in r and "共 2 人" in r
    assert bot.wl is not None and bot.wl.names() == ["OpAdmin", "ZzTestCase9"]
    assert bot.channel.commands[-2:] == ["whitelist reload", "whitelist list"]  # type: ignore[union-attr]
    assert await bot.member_names(STRANGER) == ["ZzTestCase9"]
    assert "待注册" in await bot.handle_whitelist_command(M(STRANGER), "my_wl", None)

    r = await bot.handle_whitelist_command(M(ADMIN), "wl_remove", "ZZTESTCASE9")
    assert r.startswith("🗑") and "共 1 人" in r
    assert "📭" in await bot.handle_whitelist_command(M(ADMIN), "reject", "Nobody")


async def test_unverified_write_is_reported(bot: BotCore) -> None:
    bot.channel.broken = True  # type: ignore[union-attr]
    r = await bot.handle_whitelist_command(M(ADMIN), "wl_add", "NewOne")
    assert r.startswith("⚠️") and "未能从服务端确认" in r and not leaks(r)


# ---- 6. 关键词路由与 Mod 列表 ---------------------------------------------------


async def test_keyword_routing(bot: BotCore) -> None:
    cmd, reply = await bot.handle_message(M(STRANGER, "<@!bot> mod"))
    assert (
        cmd == "mods"
        and "fabric-api" in reply
        and "lithium-fabric" in reply
        and ".jar" not in reply
    )
    cmd, reply = await bot.handle_message(M(STRANGER, "<@!bot> 白名单 列表"))
    assert cmd == "wl_list" and reply.startswith("⛔")  # 严格指令优先，不被「列表」抢走
    cmd, reply = await bot.handle_message(M(STRANGER, ""))
    assert cmd == "status" and "🔴 服务器离线" in reply  # 端口 1 必然连不上
    cmd, _ = await bot.handle_message(M(STRANGER, "随便说点什么"))
    assert cmd == "unknown"
    rows = await bot.interactions(10)
    assert rows[0]["cmd"] == "unknown" and rows[0]["openid"] == STRANGER
    assert len(await bot.interactions(10, q="mod")) >= 1


# ---- 7. 监控 / 维护状态机（旧实现无测试） ------------------------------------------


async def test_unplanned_down_and_recovery(bot: BotCore) -> None:
    pushed: Pushed = bot.pusher  # type: ignore[assignment]
    await bot.add_group(GROUP)
    up, down = (
        MCStatus(online=True, players_max=25),
        MCStatus(online=False, error="Connection refused"),
    )
    await bot.handle_transition(up)
    for _ in range(2):
        await bot.handle_transition(down)
    assert pushed == []  # 未到阈值不报
    await bot.handle_transition(down)
    assert "疑似宕机" in pushed[-1][0] and pushed[-1][1] == {GROUP}
    await bot.handle_transition(down)
    assert len(pushed) == 1  # 不重复报
    await bot.handle_transition(up)
    assert pushed[-1][0].startswith("✅ Minecraft 服务器已恢复")


async def test_planned_restart_by_watchdog_state(bot: BotCore, tmp_path: Path) -> None:
    pushed: Pushed = bot.pusher  # type: ignore[assignment]
    await bot.add_group(GROUP)
    (tmp_path / "state").write_text("stopped:mc-restart:1791500000")
    await bot.handle_transition(MCStatus(online=True))
    for _ in range(3):
        await bot.handle_transition(MCStatus(online=False))
    assert "计划维护" in pushed[-1][0]
    await bot.handle_transition(MCStatus(online=True))
    assert pushed[-1][0].startswith("✅ 维护完成")


async def test_maintenance_signal_window_and_cooldown(bot: BotCore, tmp_path: Path) -> None:
    pushed: Pushed = bot.pusher  # type: ignore[assignment]
    await bot.add_group(GROUP)
    await bot.begin_maintenance({"until": 9_999_999_999, "warn_minutes": 5})
    assert "将在 5 分钟后重启维护" in pushed[-1][0]
    await bot.begin_maintenance({"until": 9_999_999_999, "warn_minutes": 5})
    assert len(pushed) == 1  # 冷却期内不刷屏
    await bot.handle_transition(MCStatus(online=True))  # 预警阶段仍在线：静默
    await bot.handle_transition(MCStatus(online=False))  # 窗口内 1 次掉线即认定
    assert len(pushed) == 1
    await bot.handle_transition(MCStatus(online=True))
    assert pushed[-1][0].startswith("✅ 维护完成")


async def test_security_alert_only_to_admin_groups(bot: BotCore) -> None:
    pushed: Pushed = bot.pusher  # type: ignore[assignment]
    await bot.add_group(GROUP)
    alert = {"severity": "critical", "title": "X", "detail": "198.51.100.1"}
    await bot.push_security_alert(alert)
    assert pushed == []  # 没配置管理群：不推（玩家群全员可见）
    bot.s.alert_groups = {"ADMIN_GROUP"}
    await bot.push_security_alert({**alert, "severity": "warning"})
    assert pushed == []
    await bot.push_security_alert(alert)
    assert pushed[-1][1] == {"ADMIN_GROUP"}


# ---- 8. 旧数据导入 ---------------------------------------------------------------


async def test_import_legacy_is_idempotent(bot: BotCore, tmp_path: Path) -> None:
    d = tmp_path / "legacy"
    d.mkdir()
    (d / "applications.json").write_text(
        json.dumps({"pending": {"NewGuy": {"openid": "OA", "ts": 1791500000}}})
    )
    (d / "members.json").write_text(json.dumps({"members": {"OB": ["Alice", "Bob2"]}}))
    (d / "groups.json").write_text(json.dumps({"groups": ["G1", "G2"]}))
    (d / "whitelist-audit.log").write_text(
        "2026-10-08T12:00:00+0800\taction=apply\tactor=OA\tplayer=NewGuy\tresult=pending\n"
    )
    (d / "interactions.log").write_text(
        "2026-10-08 12:00:00,123\tgroup=G1\topenid=OA\tadmin=False\tcmd=status\tin=状态\tout=🟢 服务器在线 / 👥 玩家：0/25\n"
    )
    c1 = await import_legacy(bot.engine, d)
    c2 = await import_legacy(bot.engine, d)
    assert c1 == {"applications": 1, "members": 2, "groups": 2, "audit": 1, "interactions": 1}
    assert c2 == {"applications": 0, "members": 0, "groups": 0, "audit": 0, "interactions": 0}
    assert [a["name"] for a in await bot.applications()] == ["NewGuy"]
    assert sorted(await bot.member_names("OB")) == ["Alice", "Bob2"]
    assert (await bot.interactions(5))[0]["output"].startswith("🟢")


def test_mask_openid() -> None:
    assert T.mask_openid("A1B2C3D4E5F60718293A4B5C6D7E8F90") == "A1B2…8F90"
    assert T.mask_openid(None) == "-"


async def test_anonymous_players_filled_from_sessions(
    bot: BotCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcops import slp

    async def fake_ping(host: str, port: int, timeout: float = 3.0) -> slp.PingResult:
        return slp.PingResult(
            online=4,
            max=25,
            names=["Anonymous Player", "Alice", "Anonymous Player", "Anonymous Player"],
            version="1.21.11",
        )

    async def roster() -> tuple[list[str], list[str]]:
        return ["Bob", "Alice"], ["Zoe", "Sandy"]

    monkeypatch.setattr(slp, "ping", fake_ping)
    bot.roster = roster
    st = await bot.query()
    text = st.players_text()
    assert "Alice、Bob" in text and "Anonymous" not in text
    assert "🤖 假人：Zoe、Sandy" in text and "（真人 2 · 假人 2）" in text
    assert "（真人 2 · 假人 2）" in st.summary()

    async def no_fakes() -> tuple[list[str], list[str]]:
        return ["Alice"], []

    bot.roster = no_fakes
    assert "假人" not in (await bot.query()).players_text()


async def test_status_command_fetches_details(
    bot: BotCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcops import perf, slp

    tick = (
        "The game is running normallyTarget tick rate: 20.0 per second.\n"
        "Average time per tick: 5.9ms (Target: 50.0ms)Percentiles: P50: 5.2ms P95: 7.3ms"
    )

    async def fake_ping(host: str, port: int, timeout: float = 3.0) -> slp.PingResult:
        return slp.PingResult(online=0, max=25, names=[], version="1.21.11")

    async def fake_rtt(port: int = 7000, timeout: float = 3.0) -> perf.TunnelRtt:
        assert port == bot.s.frp_server_port
        return perf.TunnelRtt(9.7, 4.8, 6.4)

    async def fake_run(cmd: str) -> str:
        assert cmd == "tick query"
        return tick

    monkeypatch.setattr(slp, "ping", fake_ping)
    monkeypatch.setattr(perf, "frp_tunnel_rtt", fake_rtt)
    monkeypatch.setattr(bot.channel, "run", fake_run)
    plain = await bot.query()
    assert plain.tick is None and plain.tunnel is None  # 监控轮询不额外查
    st = await bot.query(details=True)
    assert st.tick is not None and st.tick.mspt == 5.9
    assert st.tunnel is not None and st.tunnel.rtt_ms == 9.7

    bot.s.frp_server_port = 0
    assert (await bot.query(details=True)).tunnel is None


def test_alert_group_command_aliases() -> None:
    from mcops.bot.texts import parse_strict_command

    for t in ("设为告警群", "设为警告群", "设置报警群", "<@!123> 设为告警群", "告警群"):
        assert parse_strict_command(t) == ("alert_on", None), t
    for t in ("取消告警群", "取消警告群", "关闭报警推送"):
        assert parse_strict_command(t) == ("alert_off", None), t
    assert parse_strict_command("告警") == ("alerts", None)


async def test_real_message_publishes_ui_refresh(bot: BotCore) -> None:
    """群里真实指令推一条不落库事件让面板刷新；模拟器调用不推。"""
    seen: list[Any] = []
    bot.ui_publish = seen.append
    await bot.handle_message(M(ADMIN, "帮助"), simulated=True)
    assert seen == []
    await bot.handle_message(M(ADMIN, "帮助"))
    assert [(e.type, e.data) for e in seen] == [("bot_interaction", {"cmd": "help"})]


# ---- 获批后注册期限 -------------------------------------------------------------


async def _session(bot: BotCore, name: str, at: datetime, fake: bool = False) -> None:
    async with bot.engine.begin() as conn:
        await conn.execute(
            insert(db.player_sessions).values(name=name, login_at=at, fake=fake, ip=None)
        )


async def _approve(bot: BotCore, name: str) -> str:
    await bot.handle_whitelist_command(M(STRANGER), "apply", name)
    return await bot.handle_whitelist_command(M(ADMIN), "approve", name)


async def test_activation_rule_is_explained_to_players(bot: BotCore) -> None:
    assert "2 天内" in T.help_text(False, 48) and "/register" in T.help_text(False, 48)
    assert "/register" not in T.help_text(False, 0)
    r = await bot.handle_whitelist_command(M(STRANGER), "apply", "NewOne")
    assert "自动移出白名单" in r
    r = await bot.handle_whitelist_command(M(ADMIN), "approve", "NewOne")
    assert r.startswith("✅") and "前进服并完成注册" in r
    mine = await bot.handle_whitelist_command(M(STRANGER), "my_wl", None)
    assert "待注册：NewOne" in mine
    # 管理员直接添加不受期限约束
    await bot.handle_whitelist_command(M(ADMIN), "wl_add", "DirectAdd")
    assert [g["name"] for g in await bot.grants()] == ["NewOne"]


async def test_activation_sweep_expires_activates_and_waits(bot: BotCore) -> None:
    registered: set[str] | None = {"goodone"}
    bot.registered = lambda: registered
    online: list[str] = []

    async def roster() -> tuple[list[str], list[str]]:
        return online, []

    bot.roster = roster
    for n in ("GoodOne", "LateOne", "OnlineOne", "NoRegOne"):
        await _approve(bot, n)
    now = datetime.now(UTC)
    await _session(bot, "goodone", now)  # 进服 + 已注册 → 激活
    await _session(bot, "NoRegOne", now)  # 进服但没注册 → 照样逾期
    online.append("OnlineOne")

    assert await bot.activation_sweep(now) == []  # 期限内只结清已激活的
    later = now + timedelta(hours=49)
    assert sorted(await bot.activation_sweep(later)) == ["LateOne", "NoRegOne"]
    assert bot.wl is not None
    assert sorted(bot.wl.names()) == ["GoodOne", "OnlineOne", "OpAdmin"]
    status = {g["name"]: g["status"] for g in await bot.grants()}
    assert status == {
        "GoodOne": "activated", "LateOne": "expired", "OnlineOne": "pending", "NoRegOne": "expired",
    }  # fmt: skip
    assert sorted(await bot.member_names(STRANGER)) == ["GoodOne", "OnlineOne"]

    # 下线后下一轮才移出；读不到 EasyAuth 库时整轮跳过
    online.clear()
    registered = None
    assert await bot.activation_sweep(later) == []
    registered = set()
    assert await bot.activation_sweep(later) == ["OnlineOne"]


async def test_activation_without_easyauth_and_manual_remove(bot: BotCore) -> None:
    bot.registered = None  # 没装 EasyAuth：进过服即激活
    await _approve(bot, "PlainOne")
    await _approve(bot, "GoneOne")
    now = datetime.now(UTC)
    await _session(bot, "PlainOne", now)
    await _session(bot, "GoneOne", now, fake=True)  # 假人会话不算
    await bot.handle_whitelist_command(M(ADMIN), "wl_remove", "GoneOne")
    assert await bot.activation_sweep(now + timedelta(hours=100)) == []
    status = {g["name"]: g["status"] for g in await bot.grants()}
    assert status == {"PlainOne": "activated", "GoneOne": "cancelled"}
