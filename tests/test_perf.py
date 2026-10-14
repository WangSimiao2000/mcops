"""perf：tick query / ss 解析（样本取自生产 2026-10-13）与「状态」文案。"""

from __future__ import annotations

import pytest

from mcops import perf
from mcops.bot.core import MCStatus, _tick_line

# 原版 RCON 回包把多行直接首尾相连
TICK_PROD = (
    "The game is running normallyTarget tick rate: 20.0 per second.\n"
    "Average time per tick: 5.9ms (Target: 50.0ms)Percentiles: P50: 5.2ms P95: 7.3ms "
    "P99: 68.6ms. Sample: 100"
)

SS_PROD = (
    "0      0      192.168.1.20:39628 203.0.113.10:7000\n"
    "\t cubic wscale:7,7 rto:211 rtt:10.057/4.063 ato:42 mss:1400 bytes_acked:8109484030 "
    "rcv_rtt:7.234 rcv_space:146523 minrtt:6.354 snd_wnd:627200\n"
    "0      0      192.168.1.20:40000 203.0.113.10:7000\n"
    "\t cubic rto:204 rtt:3.1/1.0 bytes_acked:1200 minrtt:2.9\n"
)


def test_parse_tick_query_prod_sample() -> None:
    t = perf.parse_tick_query(TICK_PROD)
    assert t is not None
    assert (t.mspt, t.p50, t.p95, t.p99, t.target_rate) == (5.9, 5.2, 7.3, 68.6, 20.0)
    assert t.tps == 20.0  # 未满载时 TPS 封顶为目标刻率
    assert _tick_line(t) == "🟢 TPS 20.0 · MSPT 5.9ms，P95 7.3ms"


def test_tick_overloaded_and_unparseable() -> None:
    t = perf.parse_tick_query("Target tick rate: 20.0 per second.\nAverage time per tick: 80.0ms")
    assert t is not None and t.tps == pytest.approx(12.5) and t.p95 is None
    assert _tick_line(t).startswith("🔴 TPS 12.5")
    assert _tick_line(perf.TickStats(mspt=45.0, target_rate=20.0)).startswith("🟡")
    assert perf.parse_tick_query("Unknown or incomplete command") is None
    assert perf.parse_tick_query(None) is None


def test_parse_ss_picks_busiest_connection() -> None:
    r = perf.parse_ss_rtt(SS_PROD)
    # rcv_rtt 不能被误当成 rtt；多条连接取传输量最大的 tcp_mux 主连接
    assert r == perf.TunnelRtt(rtt_ms=10.057, rttvar_ms=4.063, min_ms=6.354)
    assert perf.parse_ss_rtt("") is None


def test_summary_lines() -> None:
    st = MCStatus(
        online=True,
        players_online=1,
        players_max=25,
        version="1.21.11",
        tick=perf.parse_tick_query(TICK_PROD),
        tunnel=perf.TunnelRtt(10.057, 4.063, 6.354),
    )
    text = st.summary()
    assert "🟢 TPS 20.0 · MSPT 5.9ms" in text
    assert "📡 frp 隧道延迟：10ms（最低 6ms）" in text
    assert "延迟：" not in MCStatus(online=True).summary().replace("隧道延迟", "")
