"""按 MAC 地址找局域网设备当前的 IP（家用 NAS 走 DHCP，地址会变，又没有路由器权限做保留时用）。

做法不需要 root、不依赖 arp/ping 命令：
1. 读内核 ARP 表 /proc/net/arp，里面有这个 MAC 就直接用；
2. 没有就先连一下上次的地址（smb_host）的 445 端口，再给本机所在网段每个地址发一个 UDP 空包，
   让内核去做 ARP 解析，稍等后再读一次 ARP 表；
3. 还找不到返回 None，调用方退回配置里的地址。
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

log = logging.getLogger(__name__)

ARP_TABLE = Path("/proc/net/arp")
MAX_SWEEP_HOSTS = 1024  # 只扫 /22 及更小的网段，避免在大网段里乱发包
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_ATF_COM = 0x2  # ARP 表里 Flags 的「已解析」位


def normalize_mac(mac: str) -> str:
    """统一成小写冒号形式；接受 - 分隔或无分隔。格式不对抛 ValueError。"""
    s = mac.strip().lower().replace("-", ":")
    if ":" not in s and len(s) == 12:
        s = ":".join(s[i : i + 2] for i in range(0, 12, 2))
    if not _MAC_RE.match(s):
        raise ValueError(f"MAC 地址格式不对：{mac!r}")
    return s


def parse_arp(text: str) -> dict[str, str]:
    """解析 /proc/net/arp，返回 {mac: ip}（只要已解析的条目）。"""
    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        ip, flags, mac = parts[0], parts[2], parts[3].lower()
        try:
            if not int(flags, 16) & _ATF_COM:
                continue
        except ValueError:
            continue
        if _MAC_RE.match(mac) and mac != "00:00:00:00:00:00":
            out[mac] = ip
    return out


def sweep_hosts(networks: Iterable[str], hint: str = "") -> list[str]:
    """要探测的地址：hint 排第一，然后是各个本机网段（/22 以内）里的主机地址，去重。"""
    seen: dict[str, None] = {}
    with contextlib.suppress(ValueError):
        if hint:
            seen[str(ipaddress.IPv4Address(hint))] = None
    for net in networks:
        try:
            n = ipaddress.IPv4Network(net, strict=False)
        except ValueError:
            continue
        if n.num_addresses > MAX_SWEEP_HOSTS or n.is_loopback:
            continue
        for h in n.hosts():
            seen.setdefault(str(h), None)
    return list(seen)


async def local_networks() -> list[str]:
    """本机的 IPv4 网段（ip -4 -o addr 的 global 地址，如 192.168.1.5/24）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ip", "-4", "-o", "addr", "show", "scope", "global",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        out, _ = await asyncio.wait_for(proc.communicate(), 5)
    except (OSError, TimeoutError):
        return []
    return re.findall(r"\binet (\d+\.\d+\.\d+\.\d+/\d+)", out.decode(errors="replace"))


async def _poke(hosts: list[str], hint: str) -> None:
    """触发内核 ARP 解析：对 hint 连一下 445，对每个地址发一个 UDP 空包（discard 端口）。"""
    if hint:
        with contextlib.suppress(OSError, TimeoutError):
            _, w = await asyncio.wait_for(asyncio.open_connection(hint, 445), 1.5)
            w.close()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setblocking(False)
        for h in hosts:
            with contextlib.suppress(OSError):
                s.sendto(b"", (h, 9))
    await asyncio.sleep(1.5)


def read_arp(path: Path = ARP_TABLE) -> dict[str, str]:
    try:
        return parse_arp(path.read_text(encoding="ascii", errors="replace"))
    except OSError:
        return {}


async def find_ip_by_mac(
    mac: str,
    hint: str = "",
    *,
    arp: Callable[[], dict[str, str]] = read_arp,
    networks: Callable[[], Awaitable[list[str]]] = local_networks,
    poke: Callable[[list[str], str], Awaitable[None]] = _poke,
) -> str | None:
    """返回这个 MAC 当前的 IPv4；找不到返回 None。"""
    want = normalize_mac(mac)
    ip = arp().get(want)
    if ip:
        return ip
    hosts = sweep_hosts(await networks(), hint)
    if not hosts:
        return None
    await poke(hosts, hint)
    return arp().get(want)
