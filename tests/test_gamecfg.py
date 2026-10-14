"""M3 收尾：MOTD 同步、开服钩子、接管后 uptime。"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from mcops.gamecfg import jar_version, sync_motd
from mcops.server import _proc_started_monotonic, _run_hooks


def test_jar_version() -> None:
    assert jar_version("fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar") == "1.21.11"
    assert jar_version("server.jar") == ""


def test_sync_motd(tmp_path: Path) -> None:
    (tmp_path / "server.properties").write_text("a=1\nmotd=old\nb=2\n", encoding="utf-8")
    mm = tmp_path / "config" / "MiniMOTD"
    mm.mkdir(parents=True)
    (mm / "main.conf").write_text('x {\n  line1="old1"\n  line2="old2"\n}\n', encoding="utf-8")
    changed = sync_motd(tmp_path, "My Server", "<Gold>欢迎", r"<Green>{version} \o/", "1.21.11")
    assert changed == ["server.properties", "MiniMOTD/main.conf"]
    assert "motd=My Server\n" in (tmp_path / "server.properties").read_text(encoding="utf-8")
    conf = (mm / "main.conf").read_text(encoding="utf-8")
    assert (
        'line1="<Gold>欢迎"' in conf and r'line2="<Green>1.21.11 \o/"' in conf
    )  # 反斜杠不被当作正则转义
    assert (
        sync_motd(tmp_path, "My Server", "<Gold>欢迎", r"<Green>{version} \o/", "1.21.11") == []
    )  # 幂等


async def test_hook_failure_does_not_propagate() -> None:
    ran: list[str] = []

    async def bad() -> None:
        raise RuntimeError("boom")

    async def good() -> None:
        ran.append("good")

    await _run_hooks("t", [bad, good])
    assert ran == ["good"]


async def test_proc_start_time_of_self() -> None:
    started = _proc_started_monotonic(os.getpid())
    assert started <= time.monotonic()
    assert time.monotonic() - started < 3600  # pytest 进程不会跑一小时
    await asyncio.sleep(0)
