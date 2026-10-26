"""restic 增量备份（M4）。与 tar 冷备份并存：restic 每小时热备、可秒级挑版本；tar 每天停服冷备做兜底。

仓库默认放本机（与世界同盘只防误操作/破坏，不防硬盘损坏；异地副本另做）。
密码在 secrets.yaml 的 restic_password；丢了密码仓库就无法解密——安装脚本会提醒备份它。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcops.backup import ALWAYS_EXCLUDE, MEMBERS

log = logging.getLogger(__name__)


class ResticError(RuntimeError):
    pass


STOPPED_TAG = "stopped"
"""停服时拍的快照（每日重启、升级前）。单独计数保留，保证升级回滚、回退总有一致的停服版本可用。"""


@dataclass
class RetentionPolicy:
    keep_hourly: int = 24
    keep_daily: int = 14
    keep_weekly: int = 8
    keep_monthly: int = 6
    keep_stopped: int = 7

    def args(self) -> list[str]:
        """主策略：停服快照不在这里删（--keep-tag），由 stopped_args() 单独按个数保留。"""
        return [
            f"--keep-hourly={self.keep_hourly}",
            f"--keep-daily={self.keep_daily}",
            f"--keep-weekly={self.keep_weekly}",
            f"--keep-monthly={self.keep_monthly}",
            "--keep-tag", STOPPED_TAG,
        ]  # fmt: skip

    def stopped_args(self) -> list[str]:
        return ["--tag", STOPPED_TAG, f"--keep-last={max(self.keep_stopped, 1)}"]


def _count_removed(out: str) -> int:
    removed = 0
    for line in out.splitlines():
        try:
            groups = json.loads(line)
        except ValueError:
            continue
        if isinstance(groups, list):
            removed += sum(len(x.get("remove") or []) for x in groups)
    return removed


@dataclass
class OffsiteTarget:
    """异地副本仓库。repo 是 restic 仓库 URL，env 是访问它要的凭据（只进子进程环境，不进日志）。"""

    name: str
    """显示名，如「腾讯云 COS」「家里 NAS」。"""
    repo: str
    env: dict[str, str] = field(default_factory=dict)
    options: tuple[str, ...] = ()
    """restic -o 选项（如 s3.bucket-lookup=dns）。"""
    limit_upload_kib: int = 0
    """0 = 不限速。上传和玩家共用上行带宽，默认要限。"""
    id: str = ""
    """面板里引用这个仓库的稳定 ID（s3 / nas）。"""

    def global_args(self) -> list[str]:
        g: list[str] = []
        for o in self.options:
            g += ["-o", o]
        if self.limit_upload_kib > 0:
            g += ["--limit-upload", str(self.limit_upload_kib)]
        return g


_COS_HOST = re.compile(r"^s3:https?://cos\.([a-z0-9-]+)\.myqcloud\.com/")


def s3_target(repo: str, access_key: str, secret_key: str, limit_kib: int) -> OffsiteTarget:
    """S3 兼容存储（腾讯云 COS / 阿里云 OSS / B2 …）。COS 只支持虚拟主机式访问，需要显式指定。"""
    opts: list[str] = []
    m = _COS_HOST.match(repo)
    if m:
        opts += ["s3.bucket-lookup=dns", f"s3.region={m.group(1)}"]
    return OffsiteTarget(
        "S3 对象存储",
        repo,
        {"AWS_ACCESS_KEY_ID": access_key, "AWS_SECRET_ACCESS_KEY": secret_key},
        tuple(opts),
        limit_kib,
        "s3",
    )


_SMB_REMOTE = "mcopsnas"


def smb_target(
    host: str, share: str, path: str, user: str, obscured_password: str, rclone: str,
    limit_kib: int,
) -> OffsiteTarget:  # fmt: skip
    """局域网 SMB 共享（家用 NAS 的「网络邻居」）。经 rclone 的 smb 后端直接读写，不用挂载、不要 root。

    obscured_password 是 `rclone obscure` 的输出（rclone 配置里的密码必须是这种形式）。
    """
    sub = "/".join(p for p in (share.strip("/"), path.strip("/")) if p)
    r = _SMB_REMOTE.upper()
    env = {f"RCLONE_CONFIG_{r}_TYPE": "smb", f"RCLONE_CONFIG_{r}_HOST": host}
    if user:
        env[f"RCLONE_CONFIG_{r}_USER"] = user
    if obscured_password:
        env[f"RCLONE_CONFIG_{r}_PASS"] = obscured_password
    return OffsiteTarget(
        "局域网 NAS（SMB）",
        f"rclone:{_SMB_REMOTE}:{sub}",
        env,
        (f"rclone.program={rclone}",),
        limit_kib,
        "nas",
    )


async def rclone_obscure(rclone: str, password: str) -> str:
    """rclone obscure：密码从 stdin 传，不出现在进程参数里。"""
    proc = await asyncio.create_subprocess_exec(
        rclone, "obscure", "-",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, err = await asyncio.wait_for(proc.communicate(password.encode()), 30)
    if proc.returncode != 0:
        raise ResticError(f"rclone obscure 失败：{err.decode(errors='replace').strip()[-200:]}")
    return out.decode().strip()


@dataclass
class Restic:
    binary: str
    repo: str
    password: str
    game_dir: Path
    exclude: tuple[str, ...] = ("logs", "crash-reports")
    policy: RetentionPolicy = field(default_factory=RetentionPolicy)
    timeout_s: float = 3600

    def _env(self, target: OffsiteTarget | None = None) -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("RESTIC_", "AWS_", "RCLONE_CONFIG_"))
        }
        env.update(
            RESTIC_REPOSITORY=self.repo, RESTIC_PASSWORD=self.password, RESTIC_PROGRESS_FPS="0"
        )
        if target is not None:
            # 目标仓库作为主仓库，本地仓库作为 copy 的来源；两边同一个密码
            env.update(
                RESTIC_REPOSITORY=target.repo,
                RESTIC_FROM_REPOSITORY=self.repo,
                RESTIC_FROM_PASSWORD=self.password,
                **target.env,
            )
        cache = Path.home() / ".cache" / "restic"
        env.setdefault("RESTIC_CACHE_DIR", str(cache))
        return env

    async def _run(
        self,
        *args: str,
        ok_codes: tuple[int, ...] = (0,),
        target: OffsiteTarget | None = None,
        timeout_s: float | None = None,
    ) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            self.binary, *args,
            env=self._env(target), cwd=str(self.game_dir),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=_lower_priority,
        )  # fmt: skip
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout_s or self.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ResticError(f"restic {args[0]} 超时") from None
        except asyncio.CancelledError:
            # agent 关停：别留下孤儿 restic 进程（它会一直占着仓库锁）
            proc.kill()
            await proc.wait()
            raise
        rc = proc.returncode or 0
        if rc not in ok_codes:
            msg = err.decode(errors="replace").strip().splitlines()
            raise ResticError(f"restic {args[0]} 失败（{rc}）：{msg[-1] if msg else ''}")
        return rc, out.decode(errors="replace")

    async def _run_json(
        self,
        *args: str,
        on_status: Callable[[dict[str, Any]], None],
        target: OffsiteTarget | None = None,
        timeout_s: float | None = None,
    ) -> None:
        """边跑边读 `--json` 的 status / summary 行，用于长任务的进度显示。"""
        env = self._env(target)
        env["RESTIC_PROGRESS_FPS"] = "0.5"  # --json 下 FPS 未设置时默认 60 条/秒
        proc = await asyncio.create_subprocess_exec(
            self.binary, *args,
            env=env, cwd=str(self.game_dir),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=_lower_priority, limit=1 << 20,
        )  # fmt: skip
        assert proc.stdout is not None and proc.stderr is not None
        stdout, stderr = proc.stdout, proc.stderr

        async def pump() -> None:
            async for raw in stdout:
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                # status 约每 2 秒一条；结束时的 summary 也带最终字节数（小任务可能只有它）
                if isinstance(d, dict) and d.get("message_type") in ("status", "summary"):
                    try:
                        on_status(d)
                    except Exception:
                        log.debug("进度回调出错", exc_info=True)

        try:
            _, err = await asyncio.wait_for(
                asyncio.gather(pump(), stderr.read()), timeout_s or self.timeout_s
            )
            rc = await proc.wait()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ResticError(f"restic {args[0]} 超时") from None
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            raise
        if rc != 0:
            msg = err.decode(errors="replace").strip().splitlines()
            raise ResticError(f"restic {args[0]} 失败（{rc}）：{msg[-1] if msg else ''}")

    async def ensure_repo(self) -> bool:
        """仓库不存在则初始化；返回是否新建。restic ≥0.17：cat config 退出码 10 = 仓库不存在。"""
        rc, _ = await self._run("cat", "config", ok_codes=(0, 10))
        if rc == 0:
            return False
        if "://" not in self.repo and ":" not in self.repo.split("/")[0]:
            Path(self.repo).parent.mkdir(parents=True, exist_ok=True)
        await self._run("init")
        log.info("已初始化 restic 仓库：%s", self.repo)
        return True

    async def backup(self, tags: list[str]) -> dict[str, Any]:
        paths = [m for m in MEMBERS if (self.game_dir / m).exists()]
        excludes = sorted({e.strip("./") for e in self.exclude} | ALWAYS_EXCLUDE)
        args = ["backup", "--json", "--no-scan", "--host", "mc-server"]
        for t in tags:
            args += ["--tag", t]
        for e in excludes:
            args += ["--exclude", str(self.game_dir / e)]
        # 3 = 部分文件读不到（运行中的文件变化），快照仍然有效
        rc, out = await self._run(*args, *paths, ok_codes=(0, 3))
        summary: dict[str, Any] = {}
        for line in out.splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("message_type") == "summary":
                summary = msg
        if not summary.get("snapshot_id"):
            raise ResticError("restic backup 未返回快照 ID")
        return {
            "snapshot": summary["snapshot_id"][:8],
            "added_bytes": summary.get("data_added", 0),
            "total_bytes": summary.get("total_bytes_processed", 0),
            "files_new": summary.get("files_new", 0),
            "files_changed": summary.get("files_changed", 0),
            "seconds": round(summary.get("total_duration", 0), 1),
            "partial": rc == 3,
        }

    async def dump_zip(self, snapshot_id: str) -> asyncio.subprocess.Process:
        """把整个快照导出为 zip 流（stdout）。快照树以游戏目录为根，zip 内是 world/、mods/ …。

        调用方负责读完 stdout / 杀进程。低优先级运行，限速由下载端控制（读得慢 restic 就自然阻塞）。
        """
        if not re.fullmatch(r"[0-9a-f]{8,64}", snapshot_id):
            raise ResticError("快照 ID 不合法")
        return await asyncio.create_subprocess_exec(
            self.binary, "dump", "--quiet", "--archive", "zip", snapshot_id, "/",
            env=self._env(), cwd=str(self.game_dir),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, preexec_fn=_lower_priority,
        )  # fmt: skip

    async def copy_to(self, target: OffsiteTarget, prune: bool) -> dict[str, Any]:
        """把本地仓库里目标还没有的快照复制过去（只传目标缺的数据块），再按同一保留策略清理目标。

        目标不存在时用 --copy-chunker-params 初始化：分块参数和本地一致，去重才有效，
        否则每次复制都会把整个世界重新上传一遍。
        """
        g = target.global_args()
        new = False
        rc, _ = await self._run(*g, "cat", "config", ok_codes=(0, 10), target=target)
        if rc == 10:
            await self._run(*g, "init", "--copy-chunker-params", target=target)
            new = True
            log.info("已初始化异地仓库：%s", target.name)
        # 首次复制要上传整个仓库（几 GB），限速下可能好几个小时
        _, out = await self._run(
            *g, "copy", "--host", "mc-server", target=target, timeout_s=24 * 3600
        )
        copied = len(re.findall(r"^snapshot [0-9a-f]+ saved", out, re.M))
        removed = await self.forget(prune, target)
        return {"initialized": new, "copied": copied, "forgot": removed, "pruned": prune}

    async def forget(self, prune: bool, target: OffsiteTarget | None = None) -> int:
        """两遍：主策略（不动停服快照）→ 停服快照只留最近 keep_stopped 个。prune 放在最后一遍。"""
        g = target.global_args() if target else []
        base = [*g, "forget", "--json", "--host", "mc-server"]
        removed = 0
        for i, pol in enumerate((self.policy.args(), self.policy.stopped_args())):
            args = [*base, *pol]
            if prune and i == 1:
                args.append("--prune")
            _, out = await self._run(*args, target=target, timeout_s=6 * 3600)
            removed += _count_removed(out)
        return removed

    async def snapshots(self, target: OffsiteTarget | None = None) -> list[dict[str, Any]]:
        """快照列表（新的在前）。target 给出时列异地仓库的。"""
        g = target.global_args() if target else []
        _, out = await self._run(*g, "snapshots", "--json", target=target, timeout_s=120)
        items = json.loads(out or "[]")
        res = [
            {
                "id": s.get("short_id") or s["id"][:8],
                "time": s["time"],
                "tags": s.get("tags") or [],
                "total_bytes": (s.get("summary") or {}).get("total_bytes_processed"),
                "added_bytes": (s.get("summary") or {}).get("data_added"),
            }
            for s in items
        ]
        return sorted(res, key=lambda s: str(s["time"]), reverse=True)

    async def stats(self, target: OffsiteTarget | None = None) -> dict[str, Any]:
        g = target.global_args() if target else []
        _, out = await self._run(
            *g, "stats", "--json", "--mode", "raw-data", target=target, timeout_s=300
        )
        d = json.loads(out)
        return {
            "repo_bytes": d.get("total_size", 0),
            "uncompressed_bytes": d.get("total_uncompressed_size"),
        }

    async def restore(
        self,
        snapshot_id: str,
        dest: Path,
        members: list[str],
        target: OffsiteTarget | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """把快照里的 members（相对游戏目录，如 world / mods）解到 dest。dest 必须是空目录。

        on_progress(已恢复字节, 总字节) 大约每 2 秒调用一次。
        """
        if not re.fullmatch(r"[0-9a-f]{8,64}", snapshot_id):
            raise ResticError("快照 ID 不合法")
        dest.mkdir(parents=True, exist_ok=True)
        if any(dest.iterdir()):
            raise ResticError(f"恢复目标不是空目录：{dest}")
        g = target.global_args() if target else []
        inc: list[str] = []
        for m in members:
            inc += ["--include", "/" + m.strip("/")]
        args = [*g, "restore", f"{snapshot_id}:/", "--target", str(dest), *inc]
        if on_progress is None:
            await self._run(*args, target=target, timeout_s=6 * 3600)
            return
        cb = on_progress

        def status(d: dict[str, Any]) -> None:
            cb(int(d.get("bytes_restored") or 0), int(d.get("total_bytes") or 0))

        await self._run_json(*args, "--json", on_status=status, target=target, timeout_s=6 * 3600)


def _lower_priority() -> None:
    """备份在玩家在线时跑：降低 CPU 优先级，避免和服务端主线程抢核（压缩/哈希很吃 CPU）。"""
    os.nice(10)
