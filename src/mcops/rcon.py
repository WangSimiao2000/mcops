"""极小的异步 RCON 客户端（Source RCON 协议，Minecraft 服务端兼容）。

为什么用 RCON：命令有直接返回值，取代旧系统"tmux send-keys → 等日志 → 正则回读"的做法。

安全：只连 127.0.0.1；RCON 端口不经 frp 转发，由本机防火墙挡住局域网。
"""

from __future__ import annotations

import asyncio
import itertools
import re
import struct

_TYPE_AUTH = 3
_TYPE_COMMAND = 2
_TYPE_RESPONSE = 0
# 发送一个服务端不认识的包类型作为"结束哨兵"：服务端会按顺序回一个同 id 的包，
# 据此判断多包响应（> 4096 字节会被拆包）已经收完。
_TYPE_SENTINEL = 100
_MAX_BODY = 1446  # 服务端接受的命令请求最大长度
_COLOR_RE = re.compile("§.")


class RconError(RuntimeError):
    pass


class RconAuthError(RconError):
    pass


def strip_colors(text: str) -> str:
    return _COLOR_RE.sub("", text)


def encode_packet(req_id: int, ptype: int, body: str) -> bytes:
    payload = struct.pack("<ii", req_id, ptype) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


async def read_packet(reader: asyncio.StreamReader) -> tuple[int, int, str]:
    (length,) = struct.unpack("<i", await reader.readexactly(4))
    if not 10 <= length <= 4096 + 10:
        raise RconError(f"非法 RCON 包长度：{length}")
    data = await reader.readexactly(length)
    req_id, ptype = struct.unpack("<ii", data[:8])
    body = data[8:-2].decode("utf-8", errors="replace")
    return req_id, ptype, body


class RconClient:
    """单连接、串行化的 RCON 客户端；断线后下次调用自动重连。"""

    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0) -> None:
        self._host, self._port, self._password = host, port, password
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._ids = itertools.count(1)

    async def _connect(self) -> None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), self._timeout
        )
        rid = next(self._ids)
        writer.write(encode_packet(rid, _TYPE_AUTH, self._password))
        await writer.drain()
        # 服务端会先回一个空的 RESPONSE_VALUE（部分实现），再回 AUTH_RESPONSE。
        while True:
            got_id, ptype, _ = await asyncio.wait_for(read_packet(reader), self._timeout)
            if ptype == _TYPE_COMMAND or got_id == -1:  # AUTH_RESPONSE 与 COMMAND 同为 2
                break
        if got_id == -1:
            writer.close()
            raise RconAuthError("RCON 密码错误")
        self._reader, self._writer = reader, writer

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self._reader = self._writer = None

    async def command(self, cmd: str) -> str:
        if len(cmd.encode("utf-8")) > _MAX_BODY:
            raise RconError("命令过长")
        if "\n" in cmd or "\r" in cmd:
            raise RconError("命令不能包含换行")
        async with self._lock:
            try:
                return await self._command_locked(cmd)
            except (ConnectionError, OSError, asyncio.IncompleteReadError, TimeoutError):
                # 连接失效（服务器重启等）：重连一次再试
                await self.close()
                return await self._command_locked(cmd)
            except RconError:
                # 收到坏包后流已错位，后续命令会读到错位的响应：丢弃连接，下次重新建立。
                # 不自动重发，因为命令可能已经执行过（例如 ban）。
                await self.close()
                raise

    async def _command_locked(self, cmd: str) -> str:
        if self._writer is None:
            await self._connect()
        assert self._reader is not None and self._writer is not None
        rid, sid = next(self._ids), next(self._ids)
        # 原版服务端每次 socket read() 只处理一个包，同一 TCP 段里的第二个包会被丢弃，
        # 所以必须先收到命令的第一个响应包，再单独发送哨兵包。
        self._writer.write(encode_packet(rid, _TYPE_COMMAND, cmd))
        await self._writer.drain()
        parts: list[str] = []
        sentinel_sent = False
        while True:
            got_id, _ptype, body = await asyncio.wait_for(read_packet(self._reader), self._timeout)
            if got_id == sid:
                break
            if got_id == rid:
                parts.append(body)
                if not sentinel_sent:
                    self._writer.write(encode_packet(sid, _TYPE_SENTINEL, ""))
                    await self._writer.drain()
                    sentinel_sent = True
        return strip_colors("".join(parts))
