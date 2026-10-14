from __future__ import annotations

import asyncio
from pathlib import Path

from mcops.logtail import follow


async def _collect(path: Path, n: int, from_start: bool = False) -> list[str]:
    out: list[str] = []
    async for ln in follow(path, poll_s=0.02, from_start=from_start):
        out.append(ln)
        if len(out) >= n:
            break
    return out


async def test_follows_appends_and_rotation(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    log.write_text("old line\n")
    task = asyncio.create_task(_collect(log, 2))
    await asyncio.sleep(0.1)
    with log.open("a") as f:
        f.write("a\nb")  # 半行先不产出
    await asyncio.sleep(0.1)
    # 模拟 MC 启动时把 latest.log 归档并新建
    log.rename(tmp_path / "2026-10-09-1.log")
    await asyncio.sleep(0.1)
    log.write_text("c\n")
    got = await asyncio.wait_for(task, 3)
    # 'old line' 在开始跟随之前就存在，不产出；'b' 是未完成的半行，随旧文件一起丢弃
    assert got == ["a", "c"]


async def test_waits_for_missing_file_and_truncation(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    task = asyncio.create_task(_collect(log, 2))
    await asyncio.sleep(0.1)
    log.write_text("first\n")
    await asyncio.sleep(0.1)
    log.write_text("2nd\n")  # 截断重写（比原来短）
    assert await asyncio.wait_for(task, 3) == ["first", "2nd"]
