"""冷备份 / 保留策略（移植自 fabric-server-manager scripts/lib/backup.sh）。

保留的经验：
- `.fabric` 必须纳入备份：缺失时 Fabric 启动器退化成安装器，需要外网重新下载，外网不畅就起不来；
  `.fabric/remappedJars` 是可再生的大缓存，排除。
- 运行中备份前 `save-off` + `save-all flush`，结束（含异常）一定 `save-on`。
- 空间不足时删最旧的备份，但不低于 min_keep；清理只删过期且不低于 min_keep。
- 文件名与旧系统一致：mc-backup-YYYYmmdd_HHMMSS.tar.gz（旧的 `mc.sh backup restore` 仍可用）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import tarfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MEMBERS = [
    "world", "server.properties", "ops.json", "banned-players.json", "banned-ips.json",
    "whitelist.json", "usercache.json", "mods", "config", "EasyAuth", ".fabric",
]  # fmt: skip
ALWAYS_EXCLUDE = {".fabric/remappedJars"}
PREFIX, SUFFIX = "mc-backup-", ".tar.gz"


class BackupError(RuntimeError):
    pass


@dataclass
class BackupConfig:
    game_dir: Path
    backup_dir: Path
    keep_days: int = 7
    min_keep: int = 3
    exclude: tuple[str, ...] = ("logs", "crash-reports")


Command = Callable[[str], Awaitable[Any]]


def list_backups(backup_dir: Path) -> list[Path]:
    """最新在前。"""
    files = [p for p in backup_dir.glob(f"{PREFIX}*{SUFFIX}") if p.is_file()]
    # 按文件名里的时间戳排序（mtime 会被 touch/拷贝改掉，时钟回拨时也不可靠），解析不了才退回 mtime
    return sorted(files, key=_backup_sort_key, reverse=True)


def _backup_sort_key(p: Path) -> float:
    stamp = p.name[len(PREFIX) : -len(SUFFIX)]
    try:
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S").timestamp()
    except ValueError:
        return p.stat().st_mtime


def _dir_size(path: Path) -> int:
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def ensure_space(cfg: BackupConfig) -> None:
    world = cfg.game_dir / "world"
    needed = _dir_size(world) // 2 if world.exists() else 500 * 1024 * 1024
    while shutil.disk_usage(cfg.backup_dir).free < needed:
        backups = list_backups(cfg.backup_dir)
        if len(backups) <= cfg.min_keep:
            raise BackupError(
                f"磁盘空间不足（需要约 {needed >> 20}MB），但仅剩 {len(backups)} 份备份（最少保留 {cfg.min_keep}），拒绝删除"
            )
        oldest = backups[-1]
        log.warning("磁盘空间不足，删除最早的备份：%s", oldest.name)
        oldest.unlink()


def _make_tar(cfg: BackupConfig, dest: Path) -> None:
    excludes = {e.strip("./") for e in cfg.exclude} | ALWAYS_EXCLUDE

    def filt(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = ti.name
        for e in excludes:
            if name == e or name.startswith(e + "/"):
                return None
        return ti

    tmp = dest.with_name(dest.name + ".part")
    with tarfile.open(tmp, "w:gz", compresslevel=6) as tar:
        for m in MEMBERS:
            p = cfg.game_dir / m
            if p.exists():
                tar.add(p, arcname=m, filter=filt)
    if tmp.stat().st_size == 0:
        tmp.unlink()
        raise BackupError("备份文件为空")
    tmp.replace(dest)


@contextlib.asynccontextmanager
async def paused_saves(command: Command | None) -> AsyncIterator[None]:
    """运行中备份：save-off + save-all flush，结束（含异常）一定 save-on。command=None 表示服务端没在跑。"""
    if command is None:
        yield
        return
    await command("save-off")
    try:
        await command("save-all flush")
        await asyncio.sleep(5)  # 等 flush 落盘（沿用旧脚本）
        yield
    finally:
        try:
            await command("save-on")
        except Exception:
            log.exception("恢复自动保存失败，请手动执行 save-on")


async def create_backup(
    cfg: BackupConfig, running: bool = False, command: Command | None = None
) -> Path:
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(ensure_space, cfg)
    dest = cfg.backup_dir / f"{PREFIX}{datetime.now():%Y%m%d_%H%M%S}{SUFFIX}"
    async with paused_saves(command if running else None):
        t0 = time.monotonic()
        await asyncio.to_thread(_make_tar, cfg, dest)
    log.info(
        "备份完成：%s（%.1f MB，%.0fs）",
        dest.name,
        dest.stat().st_size / 2**20,
        time.monotonic() - t0,
    )
    return dest


def clean_backups(
    cfg: BackupConfig, days: int | None = None, now: float | None = None
) -> list[str]:
    days = cfg.keep_days if days is None else days
    now = time.time() if now is None else now
    backups = list_backups(cfg.backup_dir)
    max_delete = len(backups) - cfg.min_keep
    if max_delete <= 0:
        return []
    expired = [p for p in reversed(backups) if now - p.stat().st_mtime > days * 86400]
    deleted = []
    for p in expired[:max_delete]:
        p.unlink()
        deleted.append(p.name)
    if deleted:
        log.info("已清理 %d 个旧备份", len(deleted))
    return deleted


def backups_json(backup_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(),
        }
        for p in list_backups(backup_dir)
    ]
