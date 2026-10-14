"""回放测试：用生产日志复现 2026-10-04 冒用 Yuki_Y 的破坏事件。

生产日志含玩家数据，不入库。设置 MCOPS_REPLAY_DIR 指向含
2026-10-04-1.log.gz 的目录才会运行，否则跳过。
"""

from __future__ import annotations

import gzip
import os
from collections import Counter
from datetime import date, timedelta, timezone
from pathlib import Path

import pytest

from mcops import logparse

CST = timezone(timedelta(hours=8))
REPLAY = os.environ.get("MCOPS_REPLAY_DIR")


@pytest.mark.skipif(not REPLAY, reason="未设置 MCOPS_REPLAY_DIR")
def test_reproduces_20261004_grief_signature() -> None:
    path = Path(REPLAY or "") / "2026-10-04-1.log.gz"
    tracker = logparse.DayTracker(date(2026, 10, 4))
    evs: list[logparse.ParsedEvent] = []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = logparse.parse_line(raw, tracker.feed(raw), CST)
            if line:
                evs.extend(logparse.classify(line))

    logins = [e for e in evs if e.type == "player_login" and e.data["name"] == "Yuki_Y"]
    assert [e.ts.strftime("%H:%M:%S") for e in logins] == ["02:47:39", "02:49:12", "03:04:04"]
    cheats = Counter(e.data["kind"] for e in evs if e.type == "anticheat")
    assert cheats["moved too quickly"] >= 10
    assert cheats["kicked"] >= 1  # Flying is not enabled on this server
