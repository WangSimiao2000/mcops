"""公开状态推送：脱敏内容、历史曲线、远端只保留一个提交。"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mcops.statuspub import StatusPublisher, build_payload

RUNNING = {
    "state": "running",
    "pid": 1234,
    "uptime_s": 3600,
    "mc_version": "1.21.10",
    "players": {
        "online": 3,
        "max": 20,
        "names": ["Alice", "Bob", "bot_1"],
        "real": ["Alice", "Bob"],
        "fake": ["bot_1"],
        "real_count": 2,
        "fake_count": 1,
    },
}


def test_payload_hides_names_and_fake_players() -> None:
    p = build_payload(RUNNING, None)
    assert p["online"] is True
    assert p["players_online"] == 2  # carpet 假人不算
    assert p["players_max"] == 20
    assert p["version"] == "1.21.10"
    assert "pid" not in p
    text = json.dumps(p)
    for secret in ("Alice", "Bob", "bot_1", "1234"):
        assert secret not in text


def test_payload_offline_and_history_window() -> None:
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)
    prev = {
        "history": [
            [(now - timedelta(hours=30)).isoformat(), 5],  # 超过 24 小时，丢掉
            [(now - timedelta(hours=1)).isoformat(), 4],
            ["坏数据", 1],
        ]
    }
    p = build_payload({"state": "stopped", "players": None, "mc_version": "1.21.10"}, prev, now)
    assert p["online"] is False
    assert p["players_online"] is None and p["uptime_s"] is None
    assert [n for _, n in p["history"]] == [4, 0]


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.mark.asyncio
async def test_publish_keeps_single_commit(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git("init", "-q", "--bare", "-b", "main", cwd=remote)
    pub = StatusPublisher(
        lambda: RUNNING,
        tmp_path / "work",
        str(remote),
        tmp_path / "unused-key",
        tmp_path / "known_hosts",
    )
    await pub.publish_once()
    await pub.publish_once()
    assert _git("rev-list", "--count", "main", cwd=remote).strip() == "1"
    data = json.loads(_git("show", "main:status.json", cwd=remote))
    assert data["players_online"] == 2
    assert len(data["history"]) == 2
    assert ".nojekyll" in _git("ls-tree", "--name-only", "main", cwd=remote)
    assert pub.last_error is None and pub.last_ok is not None


def test_since_from_oldest_log(tmp_path: Path) -> None:
    from datetime import date

    from mcops.statuspub import detect_since

    logs = tmp_path / "logs"
    logs.mkdir()
    for n in ("2026-05-01-1.log.gz", "2026-04-18-3.log.gz", "latest.log", "debug.log"):
        (logs / n).touch()
    assert detect_since(tmp_path) == date(2026, 4, 18)
    assert detect_since(tmp_path / "missing") is None
    p = build_payload(RUNNING, None, since=date(2026, 4, 18))
    assert p["since"] == "2026-04-18" and p["uptime_s"] == 3600
