"""Server List Ping（SLP）：异步查询在线人数与玩家样本。移植自旧 scripts/lib/mc_ping.py。"""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass
from typing import Any

ANONYMOUS = "Anonymous Player"
"""客户端关闭「允许在服务器列表中显示」（carpet 假人默认如此）时样本里的占位名。"""


def _varint(val: int) -> bytes:
    val &= 0xFFFFFFFF
    out = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


async def _read_varint(reader: asyncio.StreamReader) -> int:
    val = shift = 0
    for _ in range(5):
        (b,) = await reader.readexactly(1)
        val |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return val
    raise ValueError("varint 过长")


@dataclass(slots=True)
class PingResult:
    online: int
    max: int
    names: list[str]
    version: str | None


async def ping(host: str, port: int, timeout: float = 3.0) -> PingResult:
    async def _do() -> PingResult:
        reader, writer = await asyncio.open_connection(host, port)
        try:
            hb = host.encode()
            hs = _varint(0) + _varint(-1) + _varint(len(hb)) + hb + struct.pack(">H", port)
            hs += _varint(1)
            writer.write(_varint(len(hs)) + hs + _varint(1) + _varint(0))
            await writer.drain()
            await _read_varint(reader)  # packet length
            await _read_varint(reader)  # packet id
            n = await _read_varint(reader)
            body: dict[str, Any] = json.loads(await reader.readexactly(n))
        finally:
            writer.close()
        players = body.get("players", {}) or {}
        return PingResult(
            online=int(players.get("online", 0)),
            max=int(players.get("max", 0)),
            names=[p["name"] for p in players.get("sample", []) or [] if "name" in p],
            version=(body.get("version") or {}).get("name"),
        )

    return await asyncio.wait_for(_do(), timeout)
