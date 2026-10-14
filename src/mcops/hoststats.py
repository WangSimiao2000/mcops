"""主机资源采样：CPU、负载、内存、磁盘、网络、MC 进程，每 10 秒一次，内存里保留最近 1 小时（360 个点）。

只读 /proc 与 statvfs，不需要额外依赖与权限。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

INTERVAL_S = 10.0
HISTORY = 360  # 1 小时
_CLK = os.sysconf("SC_CLK_TCK")
_PAGE = os.sysconf("SC_PAGE_SIZE")


def _cpu_times() -> tuple[int, int] | None:
    """(busy, total) jiffies，来自 /proc/stat 第一行。"""
    try:
        f = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        v = [int(x) for x in f]
    except (OSError, ValueError, IndexError):
        return None
    idle = v[3] + (v[4] if len(v) > 4 else 0)  # idle + iowait
    total = sum(v[:8])
    return total - idle, total


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, _, rest = ln.partition(":")
            out[k] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def _net_bytes() -> tuple[int, int]:
    rx = tx = 0
    try:
        for ln in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, _, rest = ln.partition(":")
            name = name.strip()
            if name == "lo" or name.startswith(("docker", "veth", "br-")):
                continue
            f = rest.split()
            rx += int(f[0])
            tx += int(f[8])
    except (OSError, ValueError, IndexError):
        pass
    return rx, tx


def _proc(pid: int) -> tuple[int, int] | None:
    """(utime+stime jiffies, rss bytes)。"""
    try:
        parts = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(parts[11]) + int(parts[12]), int(parts[21]) * _PAGE
    except (OSError, ValueError, IndexError):
        return None


def _uptime() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


@dataclass
class HostStats:
    paths: dict[str, Path]  # 显示名 -> 要看的路径（同一个文件系统只显示一次）
    pid_lookup: Callable[[], Awaitable[int | None]]
    history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=HISTORY))
    current: dict[str, Any] | None = None
    _prev: dict[str, Any] = field(default_factory=dict)

    def _disks(self) -> list[dict[str, Any]]:
        seen: dict[int, dict[str, Any]] = {}
        for label, p in self.paths.items():
            q = p
            while not q.exists() and q != q.parent:
                q = q.parent
            try:
                dev = q.stat().st_dev
                du = shutil.disk_usage(q)
            except OSError:
                continue
            if dev in seen:
                seen[dev]["labels"].append(label)
                continue
            seen[dev] = {
                "labels": [label],
                "path": str(q),
                "total": du.total,
                "used": du.used,
                "free": du.free,
            }
        return list(seen.values())

    async def sample(self) -> dict[str, Any]:
        now = time.monotonic()
        cpu = _cpu_times()
        mem = _meminfo()
        rx, tx = _net_bytes()
        pid = await self.pid_lookup()
        proc = _proc(pid) if pid else None
        p = self._prev
        dt = now - p["t"] if "t" in p else None

        cpu_pct = None
        if cpu and p.get("cpu") and cpu[1] > p["cpu"][1]:
            cpu_pct = 100.0 * (cpu[0] - p["cpu"][0]) / (cpu[1] - p["cpu"][1])
        net = None
        if dt and "rx" in p:
            net = {"rx_bps": max(0.0, (rx - p["rx"]) / dt), "tx_bps": max(0.0, (tx - p["tx"]) / dt)}
        mc = None
        if proc is not None:
            mc_cpu = None
            if dt and p.get("pid") == pid and p.get("proc"):
                mc_cpu = 100.0 * (proc[0] - p["proc"][0]) / _CLK / dt  # 单核 100%
            mc = {"pid": pid, "rss": proc[1], "cpu_pct": mc_cpu}
        self._prev = {"t": now, "cpu": cpu, "rx": rx, "tx": tx, "pid": pid, "proc": proc}

        total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
        used = max(0, total - avail)
        load = os.getloadavg()
        snap: dict[str, Any] = {
            "ts": time.time(),
            "cpu": {"pct": cpu_pct, "cores": os.cpu_count() or 1, "load": list(load)},
            "mem": {
                "total": total,
                "available": avail,
                "used": used,
                "swap_total": mem.get("SwapTotal", 0),
                "swap_used": max(0, mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)),
            },
            "disks": await asyncio.to_thread(self._disks),
            "net": net,
            "mc": mc,
            "uptime_s": _uptime(),
        }
        self.current = snap
        self.history.append({
            "ts": snap["ts"], "cpu": cpu_pct,
            "mem": (used / total * 100) if total else None,
            "mc_rss": mc["rss"] if mc else None, "mc_cpu": mc["cpu_pct"] if mc else None,
            "rx": net["rx_bps"] if net else None, "tx": net["tx_bps"] if net else None,
        })  # fmt: skip
        return snap

    async def loop(self) -> None:
        while True:
            await self.sample()
            await asyncio.sleep(INTERVAL_S)

    def to_json(self, history: bool = True) -> dict[str, Any]:
        return {
            "current": self.current,
            "history": list(self.history) if history else [],
            "interval_s": INTERVAL_S,
        }
