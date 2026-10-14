"""服务器性能与链路延迟的轻量探测（QQ 机器人「状态」用）。

- tick_stats：RCON `tick query`（1.20.3+ 原版命令），得到 MSPT / 百分位 / TPS。
  MSPT 是主线程每个游戏刻实际耗时，直接反映"卡不卡"；TPS = min(目标刻率, 1000 / MSPT)。
- frp_tunnel_rtt：读本机 frpc → 云主机 frps 那条 TCP 连接的内核 RTT（`ss -ti`）。
  frpc 默认 tcp_mux，所有代理（含 Minecraft）都复用这一条连接，所以它的 RTT
  就是家里到云主机这一段的真实往返时延。不发任何额外探测包，也不需要 root。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

_NUM = r"(\d+(?:\.\d+)?)"
_TARGET_RE = re.compile(rf"Target tick rate:\s*{_NUM}")
_AVG_RE = re.compile(rf"Average time per tick:\s*{_NUM}\s*ms")
_PCT_RE = re.compile(rf"P50:\s*{_NUM}\s*ms\s*P95:\s*{_NUM}\s*ms\s*P99:\s*{_NUM}\s*ms")


@dataclass(frozen=True)
class TickStats:
    mspt: float
    target_rate: float
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    frozen: bool = False

    @property
    def tps(self) -> float:
        if self.frozen:
            return 0.0
        if self.mspt <= 0:
            return self.target_rate
        return min(self.target_rate, 1000.0 / self.mspt)


def parse_tick_query(text: str | None) -> TickStats | None:
    """解析 `tick query` 输出。RCON 回包里各行可能直接首尾相连，不依赖换行。"""
    if not text:
        return None
    avg = _AVG_RE.search(text)
    if avg is None:
        return None
    target = _TARGET_RE.search(text)
    pct = _PCT_RE.search(text)
    return TickStats(
        mspt=float(avg.group(1)),
        target_rate=float(target.group(1)) if target else 20.0,
        p50=float(pct.group(1)) if pct else None,
        p95=float(pct.group(2)) if pct else None,
        p99=float(pct.group(3)) if pct else None,
        frozen="frozen" in text.lower(),
    )


async def tick_stats(
    run: Callable[[str], Awaitable[str | None]], timeout: float = 3.0
) -> TickStats | None:
    try:
        out = await asyncio.wait_for(run("tick query"), timeout)
    except (TimeoutError, OSError) as e:
        log.debug("tick query 失败：%s", e)
        return None
    return parse_tick_query(out)


@dataclass(frozen=True)
class TunnelRtt:
    rtt_ms: float
    """内核平滑 RTT（srtt）。"""
    rttvar_ms: float
    min_ms: float | None


_RTT_RE = re.compile(rf"\brtt:{_NUM}/{_NUM}")
_MINRTT_RE = re.compile(rf"\bminrtt:{_NUM}")
_BYTES_RE = re.compile(r"\bbytes_acked:(\d+)")


def parse_ss_rtt(text: str) -> TunnelRtt | None:
    """解析 `ss -tniH` 输出；有多条连接时取传输量最大的那条（tcp_mux 主连接）。"""
    best: tuple[int, TunnelRtt] | None = None
    for block in re.split(r"\n(?=\S)", text.strip()):
        m = _RTT_RE.search(block)
        if m is None:
            continue
        mn = _MINRTT_RE.search(block)
        b = _BYTES_RE.search(block)
        acked = int(b.group(1)) if b else 0
        rtt = TunnelRtt(
            rtt_ms=float(m.group(1)),
            rttvar_ms=float(m.group(2)),
            min_ms=float(mn.group(1)) if mn else None,
        )
        if best is None or acked > best[0]:
            best = (acked, rtt)
    return best[1] if best else None


async def frp_tunnel_rtt(server_port: int = 7000, timeout: float = 3.0) -> TunnelRtt | None:
    """本机到 frps（远端端口 server_port）的已建立连接的 RTT；没有连接 / 没有 ss 返回 None。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ss",
            "-tniH",
            "state",
            "established",
            f"( dport = :{server_port} )",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as e:
        log.debug("无法运行 ss：%s", e)
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    return parse_ss_rtt(out.decode(errors="replace"))
