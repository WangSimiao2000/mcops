"""测试替身：假 RCON 服务端、假进程后端。"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from collections.abc import AsyncIterator, Callable

from mcops.rcon import encode_packet


class FakeRconServer:
    """实现足够的 Source RCON 行为：认证、命令、未知类型回 "Unknown request"、长响应拆包。

    与原版服务端一致：每次 read() 只处理一个包，同一次读到的后续包被丢弃
    （真实服务端上实测的行为，客户端必须逐包等待响应）。
    """

    def __init__(self, password: str, handler: Callable[[str], str]) -> None:
        self.password = password
        self.handler = handler
        self.commands: list[str] = []
        self.port = 0
        self._server: asyncio.Server | None = None
        self.connections = 0
        self._writers: set[asyncio.StreamWriter] = set()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        authed = False
        try:
            while True:
                chunk = await reader.read(1460)
                if not chunk:
                    break
                (n,) = struct.unpack("<i", chunk[:4])
                data = chunk[4 : 4 + n]  # 同一次读到的其余字节被丢弃
                rid, ptype = struct.unpack("<ii", data[:8])
                body = data[8:-2].decode()
                if ptype == 3:
                    authed = body == self.password
                    writer.write(encode_packet(rid if authed else -1, 2, ""))
                elif not authed:
                    writer.write(encode_packet(-1, 2, ""))
                elif ptype == 2:
                    self.commands.append(body)
                    out = self.handler(body)
                    # 模拟服务端 4096 字节拆包
                    for i in range(0, max(len(out), 1), 4096):
                        writer.write(encode_packet(rid, 0, out[i : i + 4096]))
                else:
                    writer.write(encode_packet(rid, 0, f"Unknown request {ptype:x}"))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[FakeRconServer]:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        try:
            yield self
        finally:
            self._server.close()
            for w in list(self._writers):  # 模拟服务端重启：断开所有现有连接
                w.close()
            await self._server.wait_closed()


class FakeBackend:
    def __init__(self) -> None:
        self.alive = False
        self.started = 0
        self.killed = 0

    async def start(self) -> None:
        self.started += 1
        self.alive = True

    async def pid(self) -> int | None:
        return 4242 if self.alive else None

    async def terminate(self) -> None:
        self.alive = False

    async def kill(self) -> None:
        self.killed += 1
        self.alive = False
