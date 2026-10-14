from __future__ import annotations

from datetime import date, timedelta, timezone

from mcops import logparse

CST = timezone(timedelta(hours=8))
D = date(2026, 10, 4)


def events(raw: str) -> list[logparse.ParsedEvent]:
    line = logparse.parse_line(raw, D, CST)
    assert line is not None, raw
    return logparse.classify(line)


def test_login_with_real_ip_via_proxy_protocol() -> None:
    (ev,) = events(
        "[02:47:39] [Server thread/INFO]: Yuki_Y[/198.51.100.96:33583] logged in with "
        "entity id 6497 at (181.5, 139.0, 125.5)"
    )
    assert ev.type == "player_login"
    assert ev.data["name"] == "Yuki_Y"
    assert ev.data["ip"] == "198.51.100.96"
    assert ev.data["port"] == 33583
    assert ev.data["entity_id"] == 6497
    assert ev.data["pos"] == [181.5, 139.0, 125.5]
    assert ev.data["fake"] is False
    assert ev.ts.isoformat() == "2026-10-04T02:47:39+08:00"


def test_login_ipv6() -> None:
    (ev,) = events(
        "[10:00:00] [Server thread/INFO]: Steve[/2001:db8::1:51234] logged in with "
        "entity id 1 at (0.0, 64.0, 0.0)"
    )
    assert ev.data["ip"] == "2001:db8::1"
    assert ev.data["port"] == 51234


def test_carpet_fake_player_has_no_ip() -> None:
    (ev,) = events(
        "[19:50:54] [Server thread/INFO]: FARMBOT2[local] logged in with entity id 35901 "
        "at (-66.50468311614829, 247.1875, -210.87625076026603)"
    )
    assert ev.type == "player_login"
    assert ev.data["fake"] is True
    assert ev.data["ip"] is None


def test_kicked_for_flying_also_emits_anticheat() -> None:
    evs = events(
        "[02:49:07] [Server thread/INFO]: Yuki_Y lost connection: "
        "Flying is not enabled on this server"
    )
    assert [e.type for e in evs] == ["player_leave", "anticheat"]
    assert evs[1].data == {
        "name": "Yuki_Y",
        "kind": "kicked",
        "detail": "Flying is not enabled on this server",
    }


def test_easyauth_flow() -> None:
    (a,) = events(
        "[17:50:39] [Server thread/INFO]: [EasyAuth]: [LOGIN]: Player Dave is trying to login"
    )
    (b,) = events(
        "[17:50:39] [Server thread/INFO]: [EasyAuth]: [LOGIN]: Player Dave provide correct password"
    )
    assert (a.type, a.data) == ("auth_attempt", {"name": "Dave"})
    assert (b.type, b.data) == ("auth_success", {"name": "Dave"})


def test_ban_ip_and_ready_and_chat() -> None:
    (ban,) = events("[10:07:15] [Server thread/INFO]: Banned IP 198.51.100.96: 2026-10-04 grief")
    assert ban.type == "ban_ip" and ban.data["ip"] == "198.51.100.96"
    (ready,) = events('[10:00:14] [Server thread/INFO]: Done (3.648s)! For help, type "help"')
    assert ready.type == "server_ready" and ready.data["seconds"] == 3.648
    (chat,) = events("[12:00:00] [Server thread/INFO]: <Alex> 你好 hello")
    assert chat.type == "chat" and chat.data == {"name": "Alex", "message": "你好 hello"}
    (ns,) = events("[12:00:01] [Server thread/INFO]: [Not Secure] <Alex> hi")
    assert ns.type == "chat" and ns.data["message"] == "hi"


def test_moved_too_quickly() -> None:
    (ev,) = events("[02:50:00] [Server thread/WARN]: Yuki_Y moved too quickly! 12.3,4.5,6.7")
    assert ev.type == "anticheat"
    assert ev.data["kind"] == "moved too quickly"


def test_irrelevant_and_malformed_lines() -> None:
    assert events("[09:28:38] [main/INFO]: [EasyAuth]: EasyAuth mod by NikitaCartes") == []
    assert logparse.parse_line("\tat java.base/Thread.run(Thread.java:1583)", D, CST) is None
    # 名字里带非法字符不应被当成登录（防日志注入）
    assert (
        events(
            "[10:00:00] [Server thread/INFO]: <x> y[/1.1.1.1:1] logged in with entity id 1 at (0)"
        )[0].type
        == "chat"
    )


def test_day_tracker_rolls_over_midnight() -> None:
    t = logparse.DayTracker(date(2026, 10, 3))
    assert t.feed("[23:59:58] [Server thread/INFO]: a") == date(2026, 10, 3)
    assert t.feed("[00:00:02] [Server thread/INFO]: b") == date(2026, 10, 4)
    # 同一分钟内的小幅乱序不算跨天
    assert t.feed("[00:00:01] [Server thread/INFO]: c") == date(2026, 10, 4)


def test_carpet_client_handshake() -> None:
    (ev,) = events(
        "[15:36:45] [Server thread/INFO]: Player Yuki_Y joined with a matching carpet client"
    )
    assert (ev.type, ev.data) == ("carpet_client", {"name": "Yuki_Y"})


def test_easyauth_register_real_format_drops_hash() -> None:
    # 生产实际格式：名字后带 {uuid}、线程是 pool-2-thread-N、末尾是 bcrypt 哈希
    (ev,) = events(
        "[13:26:26] [pool-2-thread-8/INFO]: [EasyAuth]: [REGISTER]: Player EveX"
        "{5117bb36-953d-3a1d-bf15-bf0e59e923a6} successfully registered with password: "
        "$2a$12$Mygkbuy/27VP5.eyLZQ04.hWXilGq9cPAD9h8E7rZbMNpD2neX3GW"
    )
    assert ev.type == "auth_register"
    assert ev.data == {"name": "EveX", "uuid": "5117bb36-953d-3a1d-bf15-bf0e59e923a6"}
    assert "$2a$" not in repr(ev.data)
