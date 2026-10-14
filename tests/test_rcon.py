from __future__ import annotations

import asyncio

import pytest

from mcops.rcon import RconAuthError, RconClient, RconError
from tests.fakes import FakeRconServer


async def test_command_roundtrip_and_color_strip() -> None:
    srv = FakeRconServer("pw", lambda c: "§aThere are 0 of a max of 25 players online:")
    async with srv.running():
        c = RconClient("127.0.0.1", srv.port, "pw")
        assert await c.command("list") == "There are 0 of a max of 25 players online:"
        await c.close()
    assert srv.commands == ["list"]


async def test_multi_packet_response_is_joined() -> None:
    big = "x" * 10000
    srv = FakeRconServer("pw", lambda c: big)
    async with srv.running():
        c = RconClient("127.0.0.1", srv.port, "pw")
        assert await c.command("whitelist list") == big
        await c.close()


async def test_wrong_password() -> None:
    srv = FakeRconServer("pw", lambda c: "")
    async with srv.running():
        c = RconClient("127.0.0.1", srv.port, "nope")
        with pytest.raises(RconAuthError):
            await c.command("list")


async def test_rejects_newline_injection() -> None:
    c = RconClient("127.0.0.1", 1, "pw")
    with pytest.raises(RconError):
        await c.command("say hi\nop attacker")


async def test_reconnects_after_server_restart() -> None:
    srv = FakeRconServer("pw", lambda c: "ok")
    async with srv.running():
        c = RconClient("127.0.0.1", srv.port, "pw")
        assert await c.command("a") == "ok"
        port = srv.port
    srv2 = FakeRconServer("pw", lambda c: "ok2")
    # 在同一端口重新起服务端，模拟 MC 重启
    server = await asyncio.start_server(srv2._client, "127.0.0.1", port)
    try:
        assert await c.command("b") == "ok2"
    finally:
        await c.close()
        server.close()
        await server.wait_closed()
