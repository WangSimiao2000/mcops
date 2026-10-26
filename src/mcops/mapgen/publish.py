"""每日地图发布（agent 侧）：存档快照 -> 低优先级子进程渲染 -> 推到开了 Pages 的仓库。

- 复制存档：RCON `save-off` + `save-all flush` 后只复制修改时间 / 大小变过的区域文件，几秒钟
  后 `save-on`，不停服、玩家无感。与其他运维任务（备份、重启、Mod 更新）互斥。
- 渲染：`nice -n 19 ionice -c 3 python -m mcops.mapgen build`，算完进程退出、内存释放；
  依赖可选组 mcops[map]（numpy + pillow），agent 本身不导入它们。
- 方块颜色取自对应版本的 Mojang 客户端 jar（校验 sha1，只在本机用，不发布）。
- 推送：孤儿提交 + 强推，远端只有一个提交；git 只上传变化的瓦片。第一次推送体积大，
  等服务器没人在线时再推，不和玩家抢上行带宽。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shlex
import shutil
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from mcops import gameprops
from mcops.backup import paused_saves
from mcops.server import ConflictError

log = logging.getLogger(__name__)

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
_MOJANG_HOSTS = {"piston-meta.mojang.com", "piston-data.mojang.com", "launchermeta.mojang.com"}
TICKS_PER_MINUTE = 1200

Command = Callable[[str], Awaitable[str]]
Exclusive = Callable[[str], contextlib.AbstractAsyncContextManager[None]]


def _mojang(url: str) -> str:
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in _MOJANG_HOSTS:
        raise RuntimeError(f"下载地址不在允许范围：{u.hostname}")
    return url


def sync_regions(world: Path, dest: Path) -> dict[str, int]:
    """把 world/region 里变过的 .mca 复制到 dest/regions，删掉存档里已不存在的；顺带复制 level.dat。"""
    src_dir = world / "region"
    out = dest / "regions"
    out.mkdir(parents=True, exist_ok=True)
    copied = removed = 0
    names = set()
    for p in src_dir.glob("r.*.*.mca"):
        names.add(p.name)
        st = p.stat()
        q = out / p.name
        try:
            qs = q.stat()
            if qs.st_size == st.st_size and qs.st_mtime_ns == st.st_mtime_ns:
                continue
        except FileNotFoundError:
            pass
        tmp = out / (p.name + ".part")
        shutil.copy2(p, tmp)
        tmp.replace(q)
        copied += 1
    for q in out.glob("r.*.*.mca"):
        if q.name not in names:
            q.unlink()
            removed += 1
    if (world / "level.dat").exists():
        shutil.copy2(world / "level.dat", dest / "level.dat")
    return {"regions": len(names), "copied": copied, "removed": removed}


def world_dir(game_dir: Path) -> Path:
    try:
        props = gameprops.parse((game_dir / "server.properties").read_text(encoding="utf-8"))
    except OSError:
        props = {}
    return game_dir / (props.get("level-name") or "world")


class MapPublisher:
    def __init__(
        self,
        *,
        game_dir: Path,
        data_dir: Path,
        remote: str,
        ssh_key: Path,
        branch: str,
        run_at: str | None,
        tz: tzinfo,
        hot_minutes: int,
        workers: int,
        snapshot: Callable[[], dict[str, Any]],
        command: Command,
        exclusive: Exclusive | None = None,
        fallback_at: str = "06:30",
        announce: Callable[[str], Awaitable[Any]] | None = None,
        public_url: str | None = None,
    ) -> None:
        self.game_dir = game_dir
        self.root = data_dir / "map"
        self.cache = self.root / "cache"
        self.repo_dir = self.root / "site"
        self.known_hosts = self.root / "known_hosts"
        self.state_p = self.root / "publish-state.json"
        self.remote = remote
        self.ssh_key = ssh_key
        self.branch = branch
        # run_at=None：跟着每日定时重启走（停服时复制存档，开服后渲染）；
        # fallback_at 是兜底时间：当天还没生成过（重启关了、钩子失败、agent 当时没在跑）就补一次
        self.run_at = run_at
        self.fallback_at = fallback_at
        self.announce = announce
        self.public_url = public_url
        self.tz = tz
        self.hot_ticks = hot_minutes * TICKS_PER_MINUTE
        self.workers = workers
        self.snapshot = snapshot
        self.command = command
        self.exclusive = exclusive
        self.running = False
        self.last_ok: float | None = None
        self.last_error: str | None = None
        self.last_stats: dict[str, Any] | None = None
        self.phase: str | None = None  # 正在做哪一步，面板显示
        self.started_at: datetime | None = None
        self._bg: asyncio.Task[Any] | None = None

    # ---- 状态 ----

    def _state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, **kw: Any) -> None:
        st = {**self._state(), **kw}
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_p.with_name(self.state_p.name + ".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_p)

    # ---- 客户端 jar（取方块颜色） ----

    async def ensure_jar(self, version: str) -> Path:
        jar = self.root / "jars" / f"client-{version}.jar"
        if jar.exists():
            return jar
        jar.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
            manifest = (await http.get(MOJANG_MANIFEST)).raise_for_status().json()
            entry = next((v for v in manifest.get("versions", []) if v.get("id") == version), None)
            if entry is None:
                raise RuntimeError(f"Mojang 版本清单里没有 {version}")
            meta = (await http.get(_mojang(str(entry["url"])))).raise_for_status().json()
            client = meta["downloads"]["client"]
            tmp = jar.with_name(jar.name + ".part")
            h = hashlib.sha1()  # noqa: S324 —— Mojang 只提供 sha1 校验值
            async with http.stream("GET", _mojang(str(client["url"])), timeout=600) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes(1 << 16):
                        h.update(chunk)
                        f.write(chunk)
        if h.hexdigest() != client["sha1"]:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("客户端 jar 的 sha1 校验失败")
        tmp.replace(jar)
        for old in jar.parent.glob("client-*.jar"):  # 只留当前版本
            if old != jar:
                old.unlink()
        return jar

    # ---- 存档快照 ----

    @contextlib.asynccontextmanager
    async def _exclusive(self) -> AsyncIterator[None]:
        if self.exclusive is None:
            yield
            return
        for _ in range(60):  # 撞上备份 / 重启等任务就等，最多 30 分钟
            cm = self.exclusive("map_snapshot")
            try:
                await cm.__aenter__()
            except ConflictError:
                await asyncio.sleep(30)
                continue
            try:
                yield
            except BaseException as e:
                if not await cm.__aexit__(type(e), e, e.__traceback__):
                    raise
            else:
                await cm.__aexit__(None, None, None)
            return
        raise RuntimeError("等待其它运维任务超过 30 分钟")

    async def take_snapshot(self) -> dict[str, int]:
        running = self.snapshot().get("state") == "running"
        async with self._exclusive(), paused_saves(self.command if running else None):
            return await asyncio.to_thread(sync_regions, world_dir(self.game_dir), self.cache)

    # ---- 渲染 ----

    async def render(self, jar: Path, version: str | None) -> dict[str, Any]:
        argv = [sys.executable, "-m", "mcops.mapgen", "build", "--cache", str(self.cache),
                "--out", str(self.repo_dir), "--jar", str(jar), "--hot-ticks",
                str(self.hot_ticks), "--workers", str(self.workers)]  # fmt: skip
        if version:
            argv += ["--world-version", version]
        prefix = ["nice", "-n", "19"]
        if shutil.which("ionice"):
            prefix += ["ionice", "-c", "3"]
        proc = await asyncio.create_subprocess_exec(
            *prefix, *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )  # fmt: skip
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=4 * 3600)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("渲染超过 4 小时，已终止") from None
        text = out.decode(errors="replace").strip()
        if proc.returncode != 0:
            raise RuntimeError(f"渲染失败（退出码 {proc.returncode}）：{text[-600:]}")
        last = text.splitlines()[-1] if text else "{}"
        try:
            stats: dict[str, Any] = json.loads(last)
        except ValueError:
            stats = {"output": text[-300:]}
        return stats

    # ---- 推送 ----

    def _env(self) -> dict[str, str]:
        ssh = [
            "ssh", "-i", str(self.ssh_key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", f"UserKnownHostsFile={self.known_hosts}", "-o", "StrictHostKeyChecking=accept-new",
        ]  # fmt: skip
        return {
            **os.environ,
            "GIT_SSH_COMMAND": shlex.join(ssh),
            "GIT_AUTHOR_NAME": "mc-ops",
            "GIT_AUTHOR_EMAIL": "mc-ops@localhost",
            "GIT_COMMITTER_NAME": "mc-ops",
            "GIT_COMMITTER_EMAIL": "mc-ops@localhost",
        }

    async def _git(self, *args: str, check: bool = True, timeout: float = 600) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=self.repo_dir, env=self._env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"git {args[0]} 超时") from None
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} 失败：{err.decode(errors='replace').strip()[-300:]}")
        return out.decode(errors="replace")

    async def _wait_empty_server(self, limit_s: float = 6 * 3600) -> None:
        deadline = time.monotonic() + limit_s
        while time.monotonic() < deadline:
            snap = self.snapshot()
            players = (snap.get("players") or {}).get("real_count") or 0
            if snap.get("state") != "running" or players == 0:
                return
            log.info("地图首次推送：当前 %s 人在线，等没人时再推", players)
            await asyncio.sleep(600)

    async def push(self, stamp: str) -> None:
        if not (self.repo_dir / ".git").is_dir():
            self.repo_dir.mkdir(parents=True, exist_ok=True)
            await self._git("init", "-q", "-b", self.branch)
            await self._git("remote", "add", "origin", self.remote)
        (self.repo_dir / ".nojekyll").touch()
        readme = self.repo_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                "# mc-map\n\n由 mc-ops 每天自动渲染的服务器等轴测地图瓦片，只保留最新一次提交。\n",
                encoding="utf-8",
            )
        if not self._state().get("pushed"):
            await self._wait_empty_server()
        await self._git("checkout", "-q", "--orphan", "publish")
        await self._git("add", "-A")
        await self._git("commit", "-q", "-m", f"map {stamp}")
        await self._git("branch", "-q", "-D", self.branch, check=False)
        await self._git("branch", "-q", "-m", self.branch)
        await self._git(
            "push", "-q", "--force", "origin", f"{self.branch}:{self.branch}", timeout=3 * 3600
        )
        self._save_state(pushed=True)
        # 本地旧提交的对象没用了，定期清掉，免得 .git 越来越大
        if datetime.now(self.tz).weekday() == 0:
            await self._git("reflog", "expire", "--expire=now", "--all", check=False)
            await self._git("gc", "-q", "--prune=now", check=False, timeout=1800)

    # ---- 一次完整发布 / 定时循环 ----

    def _begin(self, phase: str) -> None:
        if self.running:
            raise RuntimeError("地图正在生成")
        self.running = True
        self.phase = phase
        self.started_at = datetime.now(UTC)

    async def run_once(self, trigger: str = "schedule") -> dict[str, Any]:
        """不停服生成一次：save-off 复制存档 → 渲染 → 推送。"""
        self._begin("复制存档")
        try:
            snap = await self.take_snapshot()
        except BaseException as e:
            self._finish(trigger, None, e)
            raise
        return await self._render_and_push(trigger, snap)

    async def on_restart_stopped(self, note: Callable[[str], None]) -> None:
        """定时重启的停服窗口里调用：此时世界已落盘、没人在写，直接复制；渲染放到开服之后。"""
        try:
            self._begin("复制存档（停服中）")
        except RuntimeError:
            note("地图：上一次还在生成，今天跳过")
            return
        try:
            snap = await asyncio.to_thread(sync_regions, world_dir(self.game_dir), self.cache)
        except Exception as e:
            self._finish("restart", None, e)
            note(f"地图：复制存档失败：{e}")
            return
        note(f"地图：复制了 {snap['copied']} 个变化的区域，开服后渲染")
        self.phase = "等待开服"
        self._bg = asyncio.create_task(self._after_start(snap))

    async def _after_start(self, snap: dict[str, int]) -> None:
        deadline = time.monotonic() + 1800
        while self.snapshot().get("state") != "running" and time.monotonic() < deadline:
            await asyncio.sleep(10)
        await asyncio.sleep(60)  # 开服后先让服务器把出生点区块加载完，再开始吃 CPU
        with contextlib.suppress(Exception):  # 结果和错误已记录在状态里
            await self._render_and_push("restart", snap)

    async def _render_and_push(self, trigger: str, snap: dict[str, int]) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        try:
            version = self.snapshot().get("mc_version") or self._state().get("version")
            if not version:
                raise RuntimeError("还不知道服务器版本（服务器开起来后再试）")
            self.phase = "准备方块颜色"
            jar = await self.ensure_jar(str(version))
            self.phase = "渲染"
            stats = await self.render(jar, str(version))
            self.phase = "推送" if self._state().get("pushed") else "推送（首次，等没人在线）"
            await self.push(datetime.now(UTC).replace(microsecond=0).isoformat())
            result = {"snapshot": snap, "render": stats, "version": str(version)}
        except BaseException as e:
            self._finish(trigger, None, e)
            raise
        self._finish(trigger, result, None)
        await self._announce(result)
        return result

    def _finish(
        self, trigger: str, result: dict[str, Any] | None, err: BaseException | None
    ) -> None:
        now = datetime.now(UTC)
        started = self.started_at or now
        error = None if err is None else f"{type(err).__name__}: {err}"[:500]
        entry: dict[str, Any] = {
            "started_at": started.replace(microsecond=0).isoformat(),
            "finished_at": now.replace(microsecond=0).isoformat(),
            "seconds": round((now - started).total_seconds()),
            "trigger": trigger,
            "ok": err is None,
            "error": error,
            "result": result,
        }
        st = self._state()
        history = [entry, *(st.get("history") or [])][:30]
        kw: dict[str, Any] = {"history": history}
        if err is None:
            self.last_ok = time.time()
            self.last_error = None
            self.last_stats = result
            kw.update(last_date=datetime.now(self.tz).date().isoformat(), last=entry)
            if result:
                kw["version"] = result.get("version")
        else:
            self.last_error = error
        self._save_state(**kw)
        self.running = False
        self.phase = None
        self.started_at = None

    async def _announce(self, result: dict[str, Any]) -> None:
        if self.announce is None:
            return
        rendered = (result.get("render") or {}).get("rendered") or {}
        changed = int(rendered.get("1") or rendered.get(1) or 0)
        if changed == 0:
            return  # 今天没人动过地图，不刷屏
        text = f"🗺️ 世界地图已更新：今天有 {changed} 个区域发生了变化\n"
        if self.public_url:
            text += f"来看看最新的样子：{self.public_url}"
        else:
            text += "去博客「服务器」板块点「世界地图」就能看到最新的样子～"
        try:
            await self.announce(text)
        except Exception as e:  # 播报失败不影响地图本身
            log.warning("地图更新播报失败：%s", e)

    def _schedule_time(self) -> str:
        return self.run_at or self.fallback_at

    def _next_run(self, now: datetime) -> datetime:
        hh, mm = map(int, self._schedule_time().split(":"))
        local = now.astimezone(self.tz)
        target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if self._state().get("last_date") == local.date().isoformat():
            # 今天已经生成过（比如 05:00 跟随重启那一路）：不管兜底时间到没到，都排到明天
            return target + timedelta(days=1)
        if target > local:
            return target
        return local  # 今天的还没跑过、时间已过：马上补跑

    def _due(self, now: datetime) -> bool:
        """用同一个 now 判断现在该不该跑，避免取两次时间在目标时刻附近出现 delay<=0 的竞态。"""
        return self._next_run(now) <= now

    def status(self) -> dict[str, Any]:
        st = self._state()
        nxt = None if self.running else self._next_run(datetime.now(UTC))
        tiles = None
        try:
            man = json.loads((self.repo_dir / "manifest.json").read_text(encoding="utf-8"))
            tiles = {
                "levels": man.get("levels"),
                "generated_at": man.get("generated_at"),
                "counts": {k: len(v) for k, v in (man.get("tiles") or {}).items()},
            }
        except (OSError, ValueError):
            pass
        return {
            "enabled": True,
            "mode": "restart" if self.run_at is None else "time",
            "schedule": self.run_at,
            "fallback_at": self.fallback_at,
            "running": self.running,
            "phase": self.phase,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "next_run": nxt.isoformat() if nxt else None,
            "pushed": bool(st.get("pushed")),
            "last": st.get("last"),
            "history": st.get("history") or [],
            "last_error": self.last_error,
            "tiles": tiles,
            "public_url": self.public_url,
            "has_preview": (self.cache / "preview.webp").exists(),
            "hot_minutes": self.hot_ticks // TICKS_PER_MINUTE,
            "announce": self.announce is not None,
        }

    def preview_path(self) -> Path:
        return self.cache / "preview.webp"

    async def loop(self) -> None:
        await asyncio.sleep(120)  # 等接管 / 首次状态轮询完成
        while True:
            now = datetime.now(UTC)
            delay = (self._next_run(now) - now).total_seconds()
            if delay > 0:
                # 分段睡、醒来重新判断：睡着期间 05:00 重启那一路可能已经生成过，
                # 一觉睡到兜底时间直接跑会导致一天生成（并播报）两次
                await asyncio.sleep(min(delay, 600))
                continue
            if self.running:  # 重启钩子那一路正在跑
                await asyncio.sleep(600)
                continue
            try:
                r = await self.run_once()
                log.info("地图已发布：%s", r)
            except Exception as e:  # 失败不影响其他功能，一小时后重试
                self.last_error = str(e)
                log.warning("地图发布失败：%s", e)
                await asyncio.sleep(3600)
