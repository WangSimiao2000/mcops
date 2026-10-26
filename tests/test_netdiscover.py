"""按 MAC 找 NAS：解析 ARP 表、探测范围、兜底逻辑。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from mcops.main import resolve_nas_host
from mcops.netdiscover import find_ip_by_mac, normalize_mac, parse_arp, sweep_hosts
from mcops.restic import ResticError

ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         aa:bb:cc:00:00:01     *        wlp2s0
192.168.1.2      0x1         0x2         02:00:5E:10:20:30     *        wlp2s0
192.168.1.50     0x1         0x0         00:00:00:00:00:00     *        wlp2s0
192.168.1.30     0x1         0x0         11:22:33:44:55:66     *        wlp2s0
"""


def test_parse_arp_keeps_only_resolved_entries() -> None:
    assert parse_arp(ARP) == {
        "aa:bb:cc:00:00:01": "192.168.1.1",
        "02:00:5e:10:20:30": "192.168.1.2",
    }


@pytest.mark.parametrize(
    "raw", ["02:00:5E:10:20:30", "02-00-5e-10-20-30", "02005e102030", " 02:00:5e:10:20:30 "]
)
def test_normalize_mac(raw: str) -> None:
    assert normalize_mac(raw) == "02:00:5e:10:20:30"


def test_normalize_mac_rejects_garbage() -> None:
    with pytest.raises(ValueError, match=r"MAC"):
        normalize_mac("02:00:5e")


def test_sweep_hosts_hint_first_and_skips_big_networks() -> None:
    hosts = sweep_hosts(["192.168.1.5/24", "10.0.0.5/8", "127.0.0.1/8"], hint="192.168.1.50")
    assert hosts[0] == "192.168.1.50"
    assert len(hosts) == 254  # hint 在网段内，去重
    assert all(h.startswith("192.168.1.") for h in hosts)
    assert sweep_hosts([], hint="nas.lan") == []  # 主机名不当 hint


def test_find_uses_arp_cache_without_probing() -> None:
    poked: list[list[str]] = []

    async def nets() -> list[str]:
        return ["192.168.1.5/24"]

    async def poke(hosts: list[str], _hint: str) -> None:
        poked.append(hosts)

    ip = asyncio.run(
        find_ip_by_mac(
            "02-00-5e-10-20-30",
            "192.168.1.50",
            arp=lambda: parse_arp(ARP),
            networks=nets,
            poke=poke,
        )
    )
    assert ip == "192.168.1.2"
    assert poked == []


def test_find_probes_then_rereads_arp() -> None:
    tables = [{}, {"02:00:5e:10:20:30": "192.168.1.7"}]
    poked: list[str] = []

    async def nets() -> list[str]:
        return ["192.168.1.5/24"]

    async def poke(hosts: list[str], hint: str) -> None:
        poked.append(hint)
        assert hosts[0] == "192.168.1.50"

    ip = asyncio.run(
        find_ip_by_mac(
            "02:00:5e:10:20:30", "192.168.1.50", arp=lambda: tables.pop(0), networks=nets, poke=poke
        )
    )
    assert ip == "192.168.1.7"
    assert poked == ["192.168.1.50"]


def test_find_returns_none_when_absent() -> None:
    async def nets() -> list[str]:
        return ["192.168.1.5/24"]

    async def poke(_h: list[str], _hint: str) -> None:
        return None

    assert (
        asyncio.run(find_ip_by_mac("02:00:5e:10:20:30", "", arp=dict, networks=nets, poke=poke))
        is None
    )


def _finder(result: str | Exception | None) -> Callable[[str, str], Awaitable[str | None]]:
    async def find(_mac: str, _hint: str) -> str | None:
        if isinstance(result, Exception):
            raise result
        return result

    return find


def test_resolve_without_mac_keeps_configured_host() -> None:
    async def never(_m: str, _h: str) -> str | None:
        raise AssertionError("没填 MAC 不应该去找")

    assert asyncio.run(resolve_nas_host("192.168.1.50", "", never)) == "192.168.1.50"


def test_resolve_follows_mac() -> None:
    got = asyncio.run(resolve_nas_host("192.168.1.50", "02:00:5e:10:20:30", _finder("192.168.1.2")))
    assert got == "192.168.1.2"


@pytest.mark.parametrize("result", [None, OSError("boom")])
def test_resolve_falls_back_to_configured_host(result: str | Exception | None) -> None:
    got = asyncio.run(resolve_nas_host("192.168.1.50", "02:00:5e:10:20:30", _finder(result)))
    assert got == "192.168.1.50"


def test_resolve_mac_only_and_not_found_fails_clearly() -> None:
    with pytest.raises(ResticError, match=r"找不到 MAC"):
        asyncio.run(resolve_nas_host("", "02:00:5e:10:20:30", _finder(None)))
