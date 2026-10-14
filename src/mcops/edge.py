"""边缘采集：经受限只读 SSH 拉取云主机 frps 的 journald（JSON），记录每条外部连接的真实 IP。

云主机侧（一次性安装，见 deploy/edge/）：
    /usr/local/bin/mcops-frps-feed   根据 SSH_ORIGINAL_COMMAND 里的游标续传 journalctl -u frps -f -o json
    mcops-ro 用户 authorized_keys:   restrict,command="/usr/local/bin/mcops-frps-feed" ssh-ed25519 ...

云主机不开新端口、不跑常驻程序；密钥泄露也只能读 frps 日志。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.events import Event, EventBus

log = logging.getLogger(__name__)

_CONN_RE = re.compile(
    r"\[(?P<proxy>[\w.-]+)\] get a user connection \[(?P<ip>[0-9a-fA-F.:]+):(?P<port>\d+)\]"
)
_LOGIN_RE = re.compile(
    r"client login info: ip \[(?P<ip>[0-9a-fA-F.:]+):(?P<port>\d+)\] version \[(?P<version>[^\]]*)\] "
    r"hostname \[(?P<hostname>[^\]]*)\]"
)
_AUTH_FAIL_RE = re.compile(r"token in login doesn't match|authorization failed", re.I)
_CURSOR_RE = re.compile(r"^[A-Za-z0-9=;_-]{1,512}$")
_CURSOR_KEY = "edge.frps.cursor"


@dataclass(slots=True)
class FrpsRecord:
    ts: datetime
    kind: str  # conn / client_login / auth_failed
    data: dict[str, Any]


def parse_journal_line(raw: str) -> tuple[str | None, FrpsRecord | None]:
    """返回 (游标, 解析结果)。"""
    try:
        j = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    cursor = j.get("__CURSOR")
    msg = j.get("MESSAGE")
    if isinstance(msg, list):  # journald 对非 UTF-8 消息输出字节数组
        msg = bytes(msg).decode("utf-8", errors="replace")
    if not isinstance(msg, str):
        return cursor, None
    try:
        ts = datetime.fromtimestamp(int(j["__REALTIME_TIMESTAMP"]) / 1e6, UTC)
    except (KeyError, ValueError):
        ts = datetime.now(UTC)
    if m := _CONN_RE.search(msg):
        return cursor, FrpsRecord(
            ts, "conn", {"proxy": m["proxy"], "ip": m["ip"], "port": int(m["port"])}
        )
    if m := _LOGIN_RE.search(msg):
        return cursor, FrpsRecord(
            ts,
            "client_login",
            {"ip": m["ip"], "version": m["version"], "hostname": m["hostname"]},
        )
    if _AUTH_FAIL_RE.search(msg):
        return cursor, FrpsRecord(ts, "auth_failed", {"message": msg[-200:]})
    return cursor, None


class EdgeCollector:
    def __init__(
        self,
        engine: AsyncEngine,
        bus: EventBus,
        host: str,
        user: str,
        key_file: Path,
        known_hosts: Path,
        port: int = 22,
        retention_days: int = 180,
        public_ports: list[Any] | None = None,
    ) -> None:
        self.engine, self.bus = engine, bus
        self.public_ports: list[Any] = list(public_ports or [])
        self.host, self.user, self.port = host, user, port
        self.key_file, self.known_hosts = key_file, known_hosts
        self.retention = timedelta(days=retention_days)
        self.connected = False
        self.last_event_ts: datetime | None = None
        self.error: str | None = None

    async def _load_cursor(self) -> str | None:
        async with self.engine.connect() as conn:
            v = (
                await conn.execute(select(db.kv.c.value).where(db.kv.c.key == _CURSOR_KEY))
            ).scalar()
        return v if isinstance(v, str) and _CURSOR_RE.match(v) else None

    async def _save_cursor(self, cursor: str) -> None:
        stmt = sqlite_insert(db.kv).values(key=_CURSOR_KEY, value=cursor)
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": cursor})
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    def _ssh_argv(self, cursor: str | None) -> list[str]:
        return [
            "ssh",
            "-T",
            "-i", str(self.key_file),
            "-p", str(self.port),
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            f"{self.user}@{self.host}",
            cursor or "",
        ]  # fmt: skip

    async def run(self) -> None:
        backoff = 5.0
        while True:
            try:
                await self._run_once()
                backoff = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                log.warning("frps 采集中断：%s，%.0fs 后重连", self.error, backoff)
            self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    async def _run_once(self) -> None:
        cursor = await self._load_cursor()
        proc = await asyncio.create_subprocess_exec(
            *self._ssh_argv(cursor),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None and proc.stderr is not None
        self.connected, self.error = True, None
        pending_cursor: str | None = None
        batch: list[dict[str, Any]] = []
        last_flush = asyncio.get_running_loop().time()
        try:
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), 5)
                except TimeoutError:
                    line = None
                if line == b"":
                    break
                if line:
                    cur, rec = parse_journal_line(line.decode("utf-8", errors="replace"))
                    if cur:
                        pending_cursor = cur
                    if rec is not None:
                        await self._handle(rec, batch)
                now = asyncio.get_running_loop().time()
                if (batch or pending_cursor) and (len(batch) >= 200 or now - last_flush > 5):
                    await self._flush(batch, pending_cursor)
                    batch.clear()
                    pending_cursor = None
                    last_flush = now
        finally:
            if batch or pending_cursor:
                await self._flush(batch, pending_cursor)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            err = (await proc.stderr.read()).decode(errors="replace").strip()
            if proc.returncode not in (0, None, -9) and err:
                raise RuntimeError(err[-300:])

    async def _handle(self, rec: FrpsRecord, batch: list[dict[str, Any]]) -> None:
        self.last_event_ts = rec.ts
        if rec.kind == "conn":
            batch.append({"ts": rec.ts, **rec.data})
            # 实时事件给规则引擎；回放旧数据（> 10 分钟前）不触发实时规则
            if datetime.now(UTC) - rec.ts < timedelta(minutes=10):
                self.bus.events.publish(Event("frps_conn", "edge", rec.data, ts=rec.ts))
        else:
            await self.bus.emit(Event(f"frps_{rec.kind}", "edge", rec.data, ts=rec.ts))

    async def _flush(self, batch: list[dict[str, Any]], cursor: str | None) -> None:
        async with self.engine.begin() as conn:
            if batch:
                await conn.execute(insert(db.frps_conns), batch)
        if cursor:
            await self._save_cursor(cursor)

    async def prune(self) -> int:
        async with self.engine.begin() as conn:
            res = await conn.execute(
                delete(db.frps_conns).where(db.frps_conns.c.ts < datetime.now(UTC) - self.retention)
            )
        return int(res.rowcount or 0)

    async def count_since(self, since: datetime) -> int:
        from sqlalchemy import func

        async with self.engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        select(func.count())
                        .select_from(db.frps_conns)
                        .where(db.frps_conns.c.ts >= since)
                    )
                ).scalar()
                or 0
            )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "connected": self.connected,
            "last_event_ts": self.last_event_ts.isoformat() if self.last_event_ts else None,
            "error": self.error,
        }
