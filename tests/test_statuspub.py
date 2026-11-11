"""公开状态推送：脱敏内容、历史曲线、远端只保留一个提交。"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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


@pytest.mark.asyncio
async def test_push_only_on_change_or_heartbeat(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git("init", "-q", "--bare", "-b", "main", cwd=remote)
    status = json.loads(json.dumps(RUNNING))
    now = [1000.0]
    pub = StatusPublisher(
        lambda: status,
        tmp_path / "work",
        str(remote),
        tmp_path / "unused-key",
        tmp_path / "known_hosts",
        interval_s=600,
        heartbeat_s=3600,
        clock=lambda: now[0],
    )

    def remote_status() -> dict[str, Any]:
        data: dict[str, Any] = json.loads(_git("show", "main:status.json", cwd=remote))
        return data

    _, pushed = await pub.tick()
    assert pushed  # 进程刚启动：先推一次
    assert remote_status()["heartbeat_s"] == 3600

    # 只有运行时长和曲线在变：不推，但曲线点记在本地
    for _ in range(3):
        now[0] += 600
        status["uptime_s"] += 600
        _, pushed = await pub.tick()
        assert not pushed
    assert len(remote_status()["history"]) == 1

    # 人数变了：马上推，期间没推的曲线点一起带上
    now[0] += 600
    status["players"]["real_count"] = 3
    _, pushed = await pub.tick()
    assert pushed
    data = remote_status()
    assert data["players_online"] == 3
    assert len(data["history"]) == 5

    # 一直没变化：满一小时（检查间隔有几秒抖动也算）保底推一次
    for i in range(5):
        now[0] += 600
        _, pushed = await pub.tick()
        assert not pushed, i
    now[0] += 595
    _, pushed = await pub.tick()
    assert pushed

    # 停服：马上推
    now[0] += 600
    status["state"] = "stopped"
    _, pushed = await pub.tick()
    assert pushed and remote_status()["online"] is False
    assert _git("rev-list", "--count", "main", cwd=remote).strip() == "1"


@pytest.mark.asyncio
async def test_failed_push_retries_next_tick(tmp_path: Path) -> None:
    pub = StatusPublisher(
        lambda: RUNNING,
        tmp_path / "work",
        str(tmp_path / "missing.git"),  # 远端不存在：推送失败
        tmp_path / "unused-key",
        tmp_path / "known_hosts",
    )
    with pytest.raises(RuntimeError):
        await pub.tick()
    assert pub.last_ok is None
    # 没推成功就不记为已推送，下一轮照样会推
    assert pub._due(build_payload(RUNNING, None))


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
