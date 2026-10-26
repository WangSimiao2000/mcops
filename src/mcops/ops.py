"""运维任务：定时重启 + 冷备份（替代 crontab 里的 mc-restart.sh）、崩溃自愈（替代每分钟的 watchdog.sh）。

每次运行记录到 job_runs，面板可查看历史与手动触发。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import shutil
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from sqlalchemy import desc, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.backup import MEMBERS, BackupConfig, clean_backups, create_backup, paused_saves
from mcops.config import Config
from mcops.events import Event, EventBus
from mcops.gamecfg import jar_version, sync_motd
from mcops.restic import STOPPED_TAG, OffsiteTarget, Restic, ResticError
from mcops.server import ConflictError, ServerController, State

log = logging.getLogger(__name__)


RESTORE_SCOPES: dict[str, list[str]] = {
    "world": ["world"],
    # .fabric 是当前版本的缓存，跟着回退反而可能和现在的服务端 jar 对不上
    "game": [m for m in MEMBERS if m != ".fabric"],
}
"""从快照回退的范围：只回退世界，或世界 + Mod + 配置 + 名单类文件。"""


@dataclass
class OpsConfig:
    restart_time: str | None = "05:00"  # 本地时间 HH:MM；None 关闭
    warn_minutes: int = 5
    crash_threshold: int = 3
    crash_window_minutes: int = 10
    restart_delay_s: float = 10.0
    restic_interval_minutes: int = 360


class Progress:
    """长步骤的字节进度；速度取最近 30 秒的滑动窗口，剩余时间按它估算。"""

    WINDOW_S = 30.0

    def __init__(self, label: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.label = label
        self._clock = clock
        self.started = clock()
        self.done = 0
        self.total = 0
        self._samples: deque[tuple[float, int]] = deque()

    def update(self, done: int, total: int) -> None:
        now = self._clock()
        self.done, self.total = max(done, 0), max(total, 0)
        self._samples.append((now, self.done))
        while len(self._samples) > 2 and now - self._samples[0][0] > self.WINDOW_S:
            self._samples.popleft()

    def rate(self) -> float | None:
        """字节/秒；样本不足时用全程平均。"""
        if len(self._samples) >= 2:
            (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
            if t1 - t0 >= 1 and b1 >= b0:
                return (b1 - b0) / (t1 - t0)
        elapsed = self._clock() - self.started
        return self.done / elapsed if elapsed >= 1 and self.done > 0 else None

    def snapshot(self) -> dict[str, Any]:
        rate = self.rate()
        left = self.total - self.done
        eta = left / rate if rate and rate > 0 and self.total > 0 else None
        return {
            "label": self.label,
            "done_bytes": self.done,
            "total_bytes": self.total,
            "percent": round(self.done / self.total * 100, 1) if self.total > 0 else None,
            "rate_bps": round(rate) if rate is not None else None,
            "eta_s": round(eta) if eta is not None else None,
            "elapsed_s": round(self._clock() - self.started),
        }


class JobRunner:
    """串行执行运维任务（同一时间只跑一个），并落库运行记录。"""

    def __init__(self, engine: AsyncEngine, publish: Callable[[Event], None] | None = None) -> None:
        self.engine = engine
        self._lock = asyncio.Lock()
        self.running: str | None = None
        # 正在跑的任务：实时日志与进度（运行记录要等任务结束才落库）
        self.live: dict[str, Any] | None = None
        self.progress: Progress | None = None
        # 只推给在线面板用于刷新界面（不落库，事件表只记业务事件）
        self.publish = publish

    def _ui(self, type_: str, data: dict[str, Any]) -> None:
        if self.publish is not None:
            self.publish(Event(type_, "ops", data))

    @contextlib.asynccontextmanager
    async def exclusive(self, name: str) -> AsyncIterator[None]:
        """与运维任务互斥的短操作（Mod 启停/更新/回退），不写运行记录。忙时立即 ConflictError。"""
        if self._lock.locked():
            raise ConflictError(f"任务 {self.running} 正在运行，请稍后再试")
        async with self._lock:
            self.running = name
            try:
                yield
            finally:
                self.running = None

    async def run(
        self,
        job: str,
        trigger: str,
        fn: Callable[[Callable[[str], None]], Awaitable[dict[str, Any] | None]],
    ) -> int:
        if self._lock.locked():
            raise ConflictError(f"任务 {self.running} 正在运行")
        async with self._lock:
            self.running = job
            async with self.engine.begin() as conn:
                res = await conn.execute(
                    insert(db.job_runs).values(
                        job=job,
                        trigger=trigger,
                        started_at=datetime.now(UTC),
                        status="running",
                        log="",
                        result={},
                    )
                )
                run_id = int(res.inserted_primary_key[0])  # type: ignore[index]
            self._ui("job_started", {"job": job, "run_id": run_id, "trigger": trigger})
            lines: list[str] = []
            self.live = {
                "job": job,
                "run_id": run_id,
                "started_at": datetime.now(UTC).isoformat(),
                "lines": lines,
            }
            self.progress = None

            def note(msg: str) -> None:
                self.progress = None  # 进入下一步，上一步的进度条作废
                lines.append(f"{datetime.now():%H:%M:%S} {msg}")
                log.info("[%s] %s", job, msg)

            status, result = "ok", None
            try:
                result = await fn(note)
            except Exception as e:
                status = "failed"
                note(f"失败：{type(e).__name__}: {e}")
                log.exception("任务 %s 失败", job)
            finally:
                self.running = None
                self.live = None
                self.progress = None
                async with self.engine.begin() as conn:
                    await conn.execute(
                        update(db.job_runs)
                        .where(db.job_runs.c.id == run_id)
                        .values(
                            finished_at=datetime.now(UTC),
                            status=status,
                            log="\n".join(lines),
                            result=result or {},
                        )
                    )
                self._ui("job_finished", {"job": job, "run_id": run_id, "status": status})
            return run_id

    def track(self, label: str) -> Progress:
        """在当前任务上挂一个进度条（下一次 note 时清除）。"""
        self.progress = Progress(label)
        return self.progress

    def live_state(self) -> dict[str, Any] | None:
        if self.live is None:
            return None
        return {
            **{k: v for k, v in self.live.items() if k != "lines"},
            "log": "\n".join(self.live["lines"][-50:]),
            "progress": self.progress.snapshot() if self.progress is not None else None,
        }

    async def history(self, limit: int = 50) -> list[dict[str, Any]]:
        j = db.job_runs.c
        async with self.engine.connect() as conn:
            rows = (
                (await conn.execute(select(db.job_runs).order_by(desc(j.id)).limit(limit)))
                .mappings()
                .all()
            )
        out = []
        for r in rows:
            d = dict(r)
            for k in ("started_at", "finished_at"):
                if d[k] is not None:
                    d[k] = (d[k].replace(tzinfo=UTC) if d[k].tzinfo is None else d[k]).isoformat()
            out.append(d)
        return out


class Ops:
    def __init__(
        self,
        engine: AsyncEngine,
        bus: EventBus,
        server: ServerController,
        backup_cfg: BackupConfig,
        cfg: OpsConfig,
        tz: tzinfo,
        announce_maintenance: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        raise_alert: Callable[..., Awaitable[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        restic: Restic | None = None,
        offsite_targets: Callable[[], Awaitable[list[OffsiteTarget]]] | None = None,
    ) -> None:
        self.restic = restic
        self.offsite_targets = offsite_targets
        self._last_offsite_prune: dict[str, float] = {}
        self._last_prune: float = 0.0
        self.restic_next: datetime | None = None
        self.engine, self.bus, self.server = engine, bus, server
        self.backup_cfg, self.cfg, self.tz = backup_cfg, cfg, tz
        self.announce_maintenance = announce_maintenance
        self.raise_alert = raise_alert
        # 定时重启的停服窗口里（备份之后、开服之前）要顺便做的事，例如复制存档给地图渲染。
        # 钩子失败只记一笔，不影响开服。
        self.restart_hooks: list[Callable[[Callable[[str], None]], Awaitable[None]]] = []
        self.sleep = sleep
        self.jobs = JobRunner(engine, bus.events.publish)
        # 异地复制可能跑好几个小时（首次全量上传），单独一把锁，不挡定时重启和 Mod 操作
        self.offsite_jobs = JobRunner(engine, bus.events.publish)
        self._crashes: deque[float] = deque()
        self.autorestart_suspended = False

    # ---- 任务 ----------------------------------------------------------------

    async def scheduled_restart(self, note: Callable[[str], None]) -> dict[str, Any]:
        """移植 mc-restart.sh：维护播报 → 游戏内倒计时 → 关服 → 冷备份 → 清理 → 开服。"""
        srv, warn = self.server, self.cfg.warn_minutes
        backup_name = None
        if srv.state is State.RUNNING:
            await self._countdown_and_stop(note, warn, "重启", "scheduled-restart", extra_s=1200)
        else:
            note(f"服务器当前为 {srv.state}，跳过倒计时直接备份")
        srv.planned_by = "scheduled-restart"  # 备份期间的停机也是计划内
        try:
            backup_err: Exception | None = None
            removed: list[str] = []
            try:
                if self.restic is not None:
                    # 已有 restic：停服时拍一个增量快照代替 tar 冷备份（只写变化的块，停服更短）
                    note("停服快照（restic）")
                    res = await self._stopped_snapshot("daily")
                    backup_name = f"restic:{res['snapshot']}"
                    # 以前的 tar 冷备份满保留天数后全部清掉，不再保底留几个
                    removed = await asyncio.to_thread(
                        clean_backups, dataclasses.replace(self.backup_cfg, min_keep=0)
                    )
                    note(
                        f"停服快照 {res['snapshot']}：新增 {res['added_bytes'] / 2**20:.1f} MB，"
                        f"用时 {res['seconds']}s；清理 {len(removed)} 个旧 tar 冷备份"
                    )
                else:
                    note("冷备份")
                    path = await create_backup(self.backup_cfg, running=False)
                    backup_name = path.name
                    removed = await asyncio.to_thread(clean_backups, self.backup_cfg)
                    note(
                        f"备份 {path.name}（{path.stat().st_size / 2**20:.0f} MB），"
                        f"清理 {len(removed)} 个旧备份"
                    )
            except Exception as e:  # 备份失败也必须把服务器开回来，任务最后再记为失败
                backup_err = e
                note(f"备份失败：{type(e).__name__}: {e}；继续开服")
            for hook in self.restart_hooks:
                try:
                    await hook(note)
                except Exception as e:
                    note(f"停服钩子失败：{type(e).__name__}: {e}")
            note("开服")
            await srv.start()
            state = await srv.wait_state(
                State.RUNNING, State.CRASHED, timeout=srv.cfg.start_ready_timeout_s + 30
            )
            if state is not State.RUNNING:
                raise RuntimeError("重启后服务器未就绪")
            note("服务器已就绪")
            if backup_err is not None:
                raise RuntimeError(f"服务器已恢复，但备份失败：{backup_err}") from backup_err
            return {"backup": backup_name, "removed": removed}
        finally:
            # 没能开回来时清掉"计划内"标记：之后的停机/崩溃要按真实故障播报
            if srv.state is not State.RUNNING:
                srv.planned_by = None

    async def _countdown_and_stop(
        self, note: Callable[[str], None], warn: int, what: str, who: str, extra_s: int
    ) -> None:
        """维护播报 → 游戏内倒计时（warn 分钟 / 1 分钟 / 10 秒）→ 关服并等待进程退出。"""
        srv = self.server
        if self.announce_maintenance is not None:
            until = time.time() + warn * 60 + extra_s
            with contextlib.suppress(Exception):
                await self.announce_maintenance({"until": until, "warn_minutes": warn})
        note(f"开始 {warn} 分钟预警")
        await srv.say(f"§e[自动维护] 服务器将在{warn}分钟后{what}")
        if warn > 1:
            await self.sleep((warn - 1) * 60)
            await srv.say(f"§c[自动维护] 服务器将在1分钟后{what}")
        await self.sleep(50)
        await srv.say(f"§c[自动维护] 服务器将在10秒后{what}！")
        await self.sleep(10)
        note("关服")
        await srv.stop(who=who)
        await srv.wait_state(State.STOPPED, timeout=srv.cfg.stop_timeout_s + 60)

    def game_upgrade(
        self, prep: Any, on_java_changed: Callable[[str], Awaitable[None]] | None = None
    ) -> Callable[[Callable[[str], None]], Awaitable[dict[str, Any]]]:
        """返回一个运维任务：倒计时 → 停服 → 冷备份 → 换文件 → 开服；失败自动回滚到旧版本。"""
        from mcops import gameversions as gv

        async def run(note: Callable[[str], None]) -> dict[str, Any]:
            srv = self.server
            old_cfg = (srv.cfg.jar, srv.cfg.java_bin)
            t = prep.target
            if srv.state is State.RUNNING:
                await self._countdown_and_stop(
                    note, self.cfg.warn_minutes, f"升级到 {t.mc}", "game-upgrade", 1800
                )
            srv.planned_by = "game-upgrade"
            snap: str | None = None
            backup: Path | None = None
            if self.restic is not None:
                note("停服快照（升级前，失败时从这里恢复世界）")
                snap = (await self._stopped_snapshot("pre-upgrade"))["snapshot"]
                backup_name = f"restic:{snap}"
            else:
                note("冷备份（升级前完整备份，失败时从这里恢复世界）")
                backup = await create_backup(self.backup_cfg, running=False)
                backup_name = backup.name
            note(f"备份完成：{backup_name}")
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            swap_dir = self.backup_cfg.game_dir / ".mcops-backup" / f"upgrade-{ts}"
            rec = await asyncio.to_thread(gv.swap_in, self.backup_cfg.game_dir, prep, swap_dir)
            note(
                f"已替换服务端 {rec.old_jar} → {rec.new_jar}，mod 更新 {len(rec.new_mods)} 个，停用 {len(prep.disable)} 个"
            )
            srv.cfg.jar, srv.cfg.java_bin = prep.jar, prep.java_bin
            try:
                note("开服（首次启动新版本会转换世界，可能比平时慢）")
                await srv.start()
                state = await srv.wait_state(
                    State.RUNNING,
                    State.CRASHED,
                    timeout=max(srv.cfg.start_ready_timeout_s, 600) + 30,
                )
                if state is not State.RUNNING:
                    raise RuntimeError("新版本开服后未就绪")
            except Exception as e:
                note(f"升级失败：{e}；开始回滚")
                with contextlib.suppress(Exception):
                    if await srv.backend.pid() is not None:
                        await srv.stop(who="game-upgrade-rollback")
                        await srv.wait_state(State.STOPPED, timeout=srv.cfg.stop_timeout_s + 60)
                srv.state = State.STOPPED
                await asyncio.to_thread(gv.swap_out, self.backup_cfg.game_dir, rec)
                if snap is not None:
                    await self._restore_world_from_snapshot(snap, swap_dir / "failed")
                else:
                    assert backup is not None
                    await asyncio.to_thread(
                        gv.restore_world, self.backup_cfg.game_dir, backup, swap_dir / "failed"
                    )
                srv.cfg.jar, srv.cfg.java_bin = old_cfg
                note("已恢复旧版本的文件和世界，用旧版本开服")
                await srv.start()
                ok = await srv.wait_state(
                    State.RUNNING, State.CRASHED, timeout=srv.cfg.start_ready_timeout_s + 30
                )
                note(
                    "旧版本已恢复运行" if ok is State.RUNNING else "旧版本也没能开起来，请人工处理"
                )
                raise RuntimeError(
                    f"升级失败并已回滚：{e}（失败现场保留在 {swap_dir.name}/failed）"
                ) from e
            finally:
                if srv.state is not State.RUNNING:
                    srv.planned_by = None
            if prep.java_bin != old_cfg[1] and on_java_changed is not None:
                await on_java_changed(prep.java_bin)
            note(f"升级完成：Minecraft {t.mc}，Fabric Loader {t.loader}")
            return {"mc": t.mc, "loader": t.loader, "backup": backup_name, "swap_dir": swap_dir.name,
                    "mods_updated": rec.new_mods, "mods_disabled": prep.disable}  # fmt: skip

        return run

    async def _stopped_snapshot(self, kind: str) -> dict[str, Any]:
        """停服状态下拍 restic 快照（标签 stopped + kind）。不在这里 forget：异地复制可能正读着仓库，
        forget 要独占锁；保留策略在下一次定时快照时一起执行。"""
        assert self.restic is not None
        if await self.restic.ensure_repo():
            log.info("已初始化 restic 仓库 %s", self.restic.repo)
        return await self.restic.backup(tags=[STOPPED_TAG, kind])

    async def _restore_world_from_snapshot(self, snap: str, quarantine: Path) -> None:
        """从本机快照解出 world/ 替换当前世界；当前 world 移到 quarantine 留作排查，不删除。"""
        assert self.restic is not None
        game = self.backup_cfg.game_dir
        tmp = quarantine.parent / "restore-world"
        shutil.rmtree(tmp, ignore_errors=True)
        await self.restic.restore(snap, tmp, ["world"])
        if not (tmp / "world" / "level.dat").is_file():
            raise ResticError(f"快照 {snap} 里没有 world/level.dat")
        quarantine.mkdir(parents=True, exist_ok=True)
        if (game / "world").exists():
            shutil.move(str(game / "world"), str(quarantine / "world"))
        shutil.move(str(tmp / "world"), str(game / "world"))
        shutil.rmtree(tmp, ignore_errors=True)

    async def manual_backup(self, note: Callable[[str], None]) -> dict[str, Any]:
        running = self.server.state is State.RUNNING and not self.server.observe
        t0 = time.monotonic()
        await self.server.broadcast("[备份] 正在打包完整备份，接下来一两分钟可能会有些卡顿")
        path = await create_backup(
            self.backup_cfg, running=running, command=self.server.rcon.command if running else None
        )
        await self.server.broadcast(
            f"[备份] 完整备份完成，用时 {time.monotonic() - t0:.0f} 秒，感谢耐心", "green"
        )
        note(f"备份 {path.name}（{path.stat().st_size / 2**20:.0f} MB）")
        return {"backup": path.name}

    async def restic_backup(self, note: Callable[[str], None]) -> dict[str, Any]:
        """热备份：运行中先 save-off + flush，restic 只上传变化的块；之后按保留策略清理，每天 prune 一次。"""
        assert self.restic is not None
        r = self.restic
        if await r.ensure_repo():
            note(f"已初始化仓库 {r.repo}")
        running = self.server.state is State.RUNNING and not self.server.observe
        if running:
            await self.server.broadcast("[备份] 正在进行自动备份，可能会有几十秒的轻微卡顿")
        async with paused_saves(self.server.rcon.command if running else None):
            res = await r.backup(tags=["auto" if running else "offline"])
        if running:
            await self.server.broadcast(
                f"[备份] 自动备份完成，用时 {res['seconds']:.0f} 秒", "green"
            )
        note(
            f"快照 {res['snapshot']}：新增 {res['added_bytes'] / 2**20:.1f} MB / 共 {res['total_bytes'] / 2**30:.2f} GB，"
            f"{res['files_new']} 新文件 {res['files_changed']} 变更，用时 {res['seconds']}s"
        )
        prune = time.time() - self._last_prune > 20 * 3600
        removed = await r.forget(prune=prune)
        if prune:
            self._last_prune = time.time()
        note(f"保留策略清理 {removed} 个快照" + ("（含 prune）" if prune else ""))
        return {**res, "forgot": removed, "pruned": prune}

    # ---- 从快照回退 ----------------------------------------------------------

    async def find_repo(self, repo: str) -> OffsiteTarget | None:
        """'local' → None（本机仓库）；'nas' / 's3' → 对应的异地仓库。"""
        if repo == "local":
            return None
        if self.offsite_targets is not None:
            for t in await self.offsite_targets():
                if t.id == repo:
                    return t
        raise KeyError(repo)

    def snapshot_restore(
        self, snapshot_id: str, scope: str, repo: str
    ) -> Callable[[Callable[[str], None]], Awaitable[dict[str, Any]]]:
        """返回一个运维任务：倒计时 → 停服 → 先给现状拍一个快照 → 解出目标快照 → 替换 → 开服；
        开不起来就换回原来的文件再开服。被替换下来的文件留在 .mcops-backup/restore-<时间>/old。"""
        members = RESTORE_SCOPES[scope]

        async def run(note: Callable[[str], None]) -> dict[str, Any]:
            assert self.restic is not None
            srv, game = self.server, self.backup_cfg.game_dir
            target = await self.find_repo(repo)
            src = target.name if target else "本机"
            snaps = await self.restic.snapshots(target)
            snap = next((x for x in snaps if str(x["id"]).startswith(snapshot_id[:8])), None)
            if snap is None:
                raise ResticError(f"{src}仓库里没有快照 {snapshot_id}")
            when = datetime.fromisoformat(str(snap["time"])).astimezone(self.tz)
            label = f"{when:%m-%d %H:%M}"
            what = "回退世界" if scope == "world" else "回退世界和 Mod/配置"
            note(f"目标：{src}快照 {snap['id']}（{label}），范围：{'、'.join(members)}")
            if srv.state is State.RUNNING:
                await self._countdown_and_stop(
                    note, self.cfg.warn_minutes, f"{what}到 {label}", "snapshot-restore", 1800
                )
            srv.planned_by = "snapshot-restore"
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            work = game / ".mcops-backup" / f"restore-{ts}"
            new, old = work / "new", work / "old"
            swapped: list[str] = []
            try:
                note("先给当前状态拍一个快照（回退后反悔可以从它恢复）")
                pre = await self.restic.backup(tags=["pre-restore"])
                note(f"当前状态已存为快照 {pre['snapshot']}")
                note(f"从{src}解出快照（{snap['id']}）")
                bar = self.jobs.track(f"从{src}解出快照")
                await self.restic.restore(
                    str(snap["id"]), new, members, target, on_progress=bar.update
                )
                if "world" in members and not (new / "world" / "level.dat").is_file():
                    raise ResticError("快照里没有 world/level.dat，放弃回退")
                old.mkdir(parents=True)
                for m in members:
                    if not (new / m).exists():
                        continue  # 快照里没有（比如当时还没装 EasyAuth）就保留现状
                    if (game / m).exists():
                        shutil.move(str(game / m), str(old / m))
                    shutil.move(str(new / m), str(game / m))
                    swapped.append(m)
                note(f"已替换：{'、'.join(swapped)}")
                note("开服")
                await srv.start()
                state = await srv.wait_state(
                    State.RUNNING, State.CRASHED, timeout=srv.cfg.start_ready_timeout_s + 30
                )
                if state is not State.RUNNING:
                    raise RuntimeError("回退后开服未就绪")
            except Exception as e:
                note(f"回退失败：{e}；换回原来的文件")
                with contextlib.suppress(Exception):
                    if await srv.backend.pid() is not None:
                        await srv.stop(who="snapshot-restore-rollback")
                        await srv.wait_state(State.STOPPED, timeout=srv.cfg.stop_timeout_s + 60)
                srv.state = State.STOPPED
                failed = work / "failed"
                for m in swapped:
                    failed.mkdir(parents=True, exist_ok=True)
                    if (game / m).exists():
                        shutil.move(str(game / m), str(failed / m))
                    if (old / m).exists():
                        shutil.move(str(old / m), str(game / m))
                note("已换回原来的文件，开服")
                await srv.start()
                ok = await srv.wait_state(
                    State.RUNNING, State.CRASHED, timeout=srv.cfg.start_ready_timeout_s + 30
                )
                note(
                    "服务器已按原状恢复运行"
                    if ok is State.RUNNING
                    else "原状也没能开起来，请人工处理"
                )
                raise RuntimeError(f"回退失败，已换回原来的文件：{e}") from e
            finally:
                if srv.state is not State.RUNNING:
                    srv.planned_by = None
                shutil.rmtree(new, ignore_errors=True)
            # 只留这一次被替换下来的文件，更早的回退现场删掉（世界几个 GB）
            for d in (game / ".mcops-backup").glob("restore-*"):
                if d != work:
                    shutil.rmtree(d, ignore_errors=True)
            note(
                f"回退完成：现在是 {label} 的{('世界' if scope == 'world' else '世界、Mod 和配置')}"
            )
            return {"snapshot": snap["id"], "time": snap["time"], "repo": repo, "scope": scope,
                    "replaced": swapped, "pre_restore_snapshot": pre["snapshot"],
                    "old_dir": str(old.relative_to(game))}  # fmt: skip

        return run

    async def offsite_copy(self, note: Callable[[str], None]) -> dict[str, Any]:
        """把本地增量仓库的新快照复制到各个异地仓库。某个目标失败不影响其他目标，最后整体记为失败并告警。"""
        assert self.restic is not None and self.offsite_targets is not None
        try:
            targets = await self.offsite_targets()
        except (ResticError, OSError) as e:
            await self._offsite_alert("配置", e)
            raise
        if not targets:
            note("没有配置异地仓库")
            return {"targets": {}}
        results: dict[str, Any] = {}
        failed: list[str] = []
        for t in targets:
            prune = time.time() - self._last_offsite_prune.get(t.name, 0.0) > 20 * 3600
            note(
                f"{t.name}：开始复制"
                + (f"（限速 {t.limit_upload_kib} KiB/s）" if t.limit_upload_kib else "")
            )
            try:
                res = await self.restic.copy_to(t, prune)
            except (ResticError, OSError) as e:
                failed.append(t.name)
                note(f"{t.name}：失败：{e}")
                await self._offsite_alert(t.name, e)
                continue
            if prune:
                self._last_offsite_prune[t.name] = time.time()
            note(
                f"{t.name}：复制 {res['copied']} 个快照"
                + ("（新建仓库）" if res["initialized"] else "")
                + f"，清理 {res['forgot']} 个"
                + ("（含 prune）" if prune else "")
            )
            results[t.name] = res
        if failed:
            raise ResticError("、".join(failed) + " 复制失败")
        return {"targets": results}

    async def _offsite_alert(self, target: str, e: BaseException) -> None:
        if self.raise_alert:
            await self.raise_alert(
                "backup_offsite_failed",
                datetime.now(UTC),
                f"异地备份失败：{target}",
                f"{e}。本地快照不受影响；请检查网络、凭据和目标存储空间。",
                target,
                {"target": target, "error": str(e)[:500]},
            )

    async def restic_loop(self) -> None:
        if self.restic is None:
            return
        interval = self.cfg.restic_interval_minutes * 60
        # 接着上一次成功快照的节奏走：agent 重启/升级不会立刻多拍一次
        last = await self._last_job_start("restic_backup")
        wait = (
            120.0
            if last is None
            else (last + timedelta(seconds=interval) - datetime.now(UTC)).total_seconds()
        )
        self.restic_next = datetime.now(UTC) + timedelta(seconds=max(wait, 120))
        await asyncio.sleep(max(wait, 120))
        while True:
            self.restic_next = None
            if self.server.state in (State.RUNNING, State.STOPPED):
                with contextlib.suppress(ConflictError):
                    await self.jobs.run("restic_backup", "schedule", self.restic_backup)
            if self.offsite_targets is not None:
                with contextlib.suppress(ConflictError):
                    await self.offsite_jobs.run("offsite_copy", "schedule", self.offsite_copy)
            self.restic_next = datetime.now(UTC) + timedelta(seconds=interval)
            await asyncio.sleep(interval)

    async def _last_job_start(self, job: str) -> datetime | None:
        j = db.job_runs.c
        async with self.engine.connect() as conn:
            v = (
                await conn.execute(
                    select(j.started_at)
                    .where(j.job == job, j.status == "ok")
                    .order_by(desc(j.id))
                    .limit(1)
                )
            ).scalar()
        if not isinstance(v, datetime):
            return None
        return v if v.tzinfo else v.replace(tzinfo=UTC)

    # ---- 调度 ----------------------------------------------------------------

    def next_restart(self, now: datetime | None = None) -> datetime | None:
        if not self.cfg.restart_time:
            return None
        hh, mm = map(int, self.cfg.restart_time.split(":"))
        now = (now or datetime.now(UTC)).astimezone(self.tz)
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    async def scheduler_loop(self) -> None:
        while True:
            nxt = self.next_restart()
            if nxt is None:
                await asyncio.sleep(3600)
                continue
            delay = (nxt - datetime.now(UTC)).total_seconds()
            log.info("下次定时重启：%s（%.0f 秒后）", nxt.isoformat(), delay)
            await asyncio.sleep(max(delay, 1))
            # 撞上其它任务（手动备份、Mod 更新）就等它结束再跑，最多等 30 分钟，不跳过当天的重启
            for _ in range(60):
                try:
                    await self.jobs.run("scheduled_restart", "schedule", self.scheduled_restart)
                    break
                except ConflictError:
                    log.info("定时重启：任务 %s 正在运行，30 秒后重试", self.jobs.running)
                    await asyncio.sleep(30)
            else:
                log.error("定时重启：等待其它任务超过 30 分钟，今天跳过")
            await asyncio.sleep(61)  # 越过这一分钟，避免重复触发

    # ---- 崩溃自愈 -------------------------------------------------------------

    async def watchdog_loop(self) -> None:
        """移植 watchdog.sh：窗口内崩溃次数未达阈值则自动拉起，否则停止自愈并发严重告警。"""
        async with self.bus.events.subscribe() as q:
            while True:
                ev = await q.get()
                if ev.type in ("server_crashed", "server_start_failed"):
                    await self._on_crash(ev)
                elif ev.type == "server_foreign_instance" and self.raise_alert:
                    await self.raise_alert(
                        "server_foreign_instance",
                        ev.ts,
                        "mc-ops 之外已有服务端在运行，未启动",
                        f"{ev.data.get('reason', '')}。请检查是否有遗留的开机脚本或系统级服务"
                        "（如 /etc/systemd/system 下的旧单元、crontab @reboot、tmux），"
                        "停掉后在面板里重新开服。",
                        None,
                        dict(ev.data),
                    )
                elif ev.type == "server_stop_failed" and self.raise_alert:
                    await self.raise_alert(
                        "server_stop_failed",
                        ev.ts,
                        "关服失败，服务端进程仍在运行",
                        f"{ev.data.get('error', '')}。已恢复为运行中，请在面板查看控制台。",
                        None,
                        dict(ev.data),
                    )
                elif ev.type == "server_ready":
                    pass

    async def _on_crash(self, ev: Event) -> None:
        now = time.monotonic()
        self._crashes.append(now)
        window = self.cfg.crash_window_minutes * 60
        while self._crashes and now - self._crashes[0] > window:
            self._crashes.popleft()
        n = len(self._crashes)
        if n >= self.cfg.crash_threshold:
            self.autorestart_suspended = True
            if self.raise_alert:
                await self.raise_alert(
                    "server_crash_loop",
                    ev.ts,
                    f"服务器 {self.cfg.crash_window_minutes} 分钟内崩溃 {n} 次，已停止自动重启",
                    "请登录面板查看控制台与崩溃报告后手动处理。",
                    None,
                    {"count": n, "event": ev.type},
                )
            return
        if self.raise_alert:
            await self.raise_alert(
                "server_crash",
                ev.ts,
                "服务器意外停止，正在自动重启",
                f"事件：{ev.type}；窗口内第 {n} 次",
                None,
                {"count": n},
            )
        await self.sleep(self.cfg.restart_delay_s)
        if self.server.state in (State.CRASHED, State.STOPPED) and not self.autorestart_suspended:
            with contextlib.suppress(ConflictError):
                await self.server.start()
                await self.bus.emit(Event("server_autorestart", "watchdog", {"count": n}))

    # ---- 期望状态（开机自启） ---------------------------------------------------

    async def set_desired(self, state: str) -> None:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(db.kv).values(key="server.desired", value=state)
        async with self.engine.begin() as conn:
            await conn.execute(
                stmt.on_conflict_do_update(index_elements=["key"], set_={"value": state})
            )

    async def desired(self) -> str:
        async with self.engine.connect() as conn:
            v = (
                await conn.execute(select(db.kv.c.value).where(db.kv.c.key == "server.desired"))
            ).scalar()
        return str(v) if v else "running"

    async def recover_after_restart(self) -> None:
        """agent 启动时收拾上一个进程没来得及收尾的状态（例如被 OOM 直接 kill，finally 没跑）。

        - 运行记录里残留的 running 改为 interrupted，面板不会一直显示"运行中"；
        - 接管到正在运行的服务端时补一次 save-on：上一个进程若死在备份的 save-off 窗口里，
          世界会一直不落盘。save-on 在已开启时是无害的空操作。
        """
        async with self.engine.begin() as conn:
            res = await conn.execute(
                update(db.job_runs)
                .where(db.job_runs.c.status == "running")
                .values(status="interrupted", finished_at=datetime.now(UTC))
            )
        if res.rowcount:
            log.warning("上次 agent 退出时有 %d 个任务未完成，已标记为 interrupted", res.rowcount)
        if self.server.state is State.RUNNING and not self.server.observe:
            try:
                await self.server.rcon.command("save-on")
            except Exception as e:
                log.warning("接管后补发 save-on 失败：%s", e)

    async def autostart(self) -> None:
        """agent 启动时：服务端没在跑、且管理员没有手动关服，就拉起（主机重启后自动开服）。"""
        if not (self.server.cfg.game_dir / self.server.cfg.jar).is_file():
            log.info(
                "还没有安装服务端（%s），跳过自动开服；请在面板总览页用安装向导安装",
                self.server.cfg.jar,
            )
            return
        if (
            self.server.state in (State.STOPPED, State.CRASHED)
            and await self.desired() == "running"
        ):
            log.info("期望状态为运行，自动启动服务端")
            with contextlib.suppress(ConflictError):
                await self.server.start()
                await self.bus.emit(Event("server_autostart", "ops"))

    def reset_watchdog(self) -> None:
        self._crashes.clear()
        self.autorestart_suspended = False


def install_game_hooks(cfg: Config, server: ServerController) -> None:
    """开服前同步 MOTD、就绪后设置出生点（旧 cmd_start 的行为）。"""
    o, game_dir = cfg.ops, cfg.server.game_dir

    if o.motd is not None:
        motd = o.motd

        async def motd_hook() -> None:
            jars = sorted(game_dir.glob(cfg.server.jar))
            ver = jar_version(jars[-1].name) if jars else ""
            await asyncio.to_thread(
                sync_motd, game_dir, motd.server_list, motd.line1, motd.line2, ver
            )

        server.pre_start_hooks.append(motd_hook)

    if o.spawn is not None:
        x, y, z = o.spawn
        # 1.21.9+ 语法：setworldspawn <pos> [<yaw> <pitch>]
        cmd = f"setworldspawn {x} {y} {z}"
        if o.spawn_yaw is not None:
            cmd += f" {o.spawn_yaw:g} 0"

        async def spawn_hook() -> None:
            out = await server.rcon.command(cmd)
            log.info("%s：%s", cmd, out.strip())

        server.post_ready_hooks.append(spawn_hook)
