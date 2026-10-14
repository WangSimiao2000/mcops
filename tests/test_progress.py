"""运维任务的实时进度：滑动窗口速度、剩余时间、随 note 清除、live_state。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from mcops.db import open_db
from mcops.ops import JobRunner, Progress


def test_progress_rate_window_and_eta() -> None:
    now = [0.0]
    p = Progress("解出快照", clock=lambda: now[0])
    assert p.snapshot()["percent"] is None and p.snapshot()["eta_s"] is None
    p.update(0, 1000)
    now[0] = 10
    p.update(500, 1000)  # 50 B/s
    snap = p.snapshot()
    assert snap["percent"] == 50.0 and snap["rate_bps"] == 50 and snap["eta_s"] == 10
    # 变慢后，速度按最近 30 秒算，而不是被开头的快速段拉高
    for t in range(20, 80, 10):
        now[0] = t
        p.update(500 + (t - 10) // 10 * 10, 1000)  # 之后每秒 1 B
    snap = p.snapshot()
    assert snap["rate_bps"] == 1
    assert snap["eta_s"] == 1000 - 560
    assert snap["elapsed_s"] == 70


@pytest.fixture
async def runner(tmp_path: Path) -> AsyncIterator[JobRunner]:
    engine = await open_db(tmp_path / "t.db")
    yield JobRunner(engine)
    await engine.dispose()


async def test_live_state_tracks_running_job(runner: JobRunner) -> None:
    states: list[Any] = []

    async def job(note: Any) -> dict[str, Any]:
        note("第一步")
        bar = runner.track("解出快照")
        bar.update(10, 40)
        states.append(runner.live_state())
        note("第二步")  # 进入下一步：进度条清除
        states.append(runner.live_state())
        return {}

    assert runner.live_state() is None
    await runner.run("snapshot_restore", "test", job)
    first, second = states
    assert first["job"] == "snapshot_restore" and "第一步" in first["log"]
    assert first["progress"]["label"] == "解出快照" and first["progress"]["percent"] == 25.0
    assert second["progress"] is None and "第二步" in second["log"]
    assert runner.live_state() is None  # 结束后清空
