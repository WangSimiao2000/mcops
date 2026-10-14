"""API：诊断 / 首次安装向导 / mc-ops 自身升级（P4）。"""

# 注意：本模块特意不启用 `from __future__ import annotations`（同 api_mods.py 的原因）。

import asyncio
import os
import shutil
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from mcops.api_game import VER


class SetupBody(BaseModel):
    mc: str = Field(pattern=VER)
    loader: str = Field(pattern=VER)
    accept_eula: bool
    memory_gb: int = Field(ge=1, le=64)
    motd: str = Field(default="A Minecraft Server", max_length=120)
    max_players: int = Field(default=20, ge=1, le=1000)
    online_mode: bool = False
    white_list: bool = True
    difficulty: str = Field(default="normal", pattern="^(peaceful|easy|normal|hard)$")
    view_distance: int = Field(default=10, ge=3, le=32)
    port: int = Field(default=25565, ge=1, le=65535)
    java_mirror: str = Field(default="tuna", pattern="^(tuna|official)$")


class InstallRevBody(BaseModel):
    revision: str = Field(pattern=r"^[0-9a-f]{7,40}$")


def register(app: FastAPI) -> None:
    from mcops import appsetup, diagnostics
    from mcops import gameversions as gv
    from mcops import mods as mods_mod
    from mcops.api import Services, SessionDep, SvcDep

    def _http(s: Services) -> Any:
        return getattr(s, "mods_http", None) or mods_mod._default_http

    def _download(s: Services) -> Any:
        return getattr(s, "mods_download", None) or mods_mod._default_download

    def _runner(s: Services) -> Any:
        return getattr(s, "game_runner", None) or gv._default_runner

    @app.get("/api/system/diagnostics")
    async def diag(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return {"items": await diagnostics.run_checks(s)}

    # ---- 首次安装 ----

    @app.get("/api/setup/status")
    async def setup_status(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        game = s.server.cfg.game_dir
        return {
            "needs_setup": gv.current_jar(game) is None,
            "game_dir": str(game),
            "dir_empty": not game.exists()
            or not any(p for p in game.iterdir() if p.name not in {"mods", "logs"}),
            "memory_total_gb": int(
                (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 2**30
            ),
            "running_job": s.ops.jobs.running if s.ops is not None else None,
        }

    @app.post("/api/setup/install", status_code=202)
    async def setup_install(body: SetupBody, sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        if s.ops is None or s.config is None or s.secrets_file is None:
            raise HTTPException(409, "观测模式下不能安装服务端")
        if not body.accept_eula:
            raise HTTPException(422, "必须同意 Minecraft EULA 才能安装")
        game = s.server.cfg.game_dir
        if gv.current_jar(game) is not None:
            raise HTTPException(409, "已经装有服务端，请改用「游戏版本」升级")
        if s.ops.jobs.running:
            raise HTTPException(409, f"任务 {s.ops.jobs.running} 正在运行")
        versions = await gv.list_versions(_http(s))
        target = gv.Target(body.mc, body.loader, str(versions["installer"]))
        data_dir = s.config.running.data_dir
        game.mkdir(parents=True, exist_ok=True)

        async def job(note: Any) -> dict[str, Any]:
            srv = s.server
            plan = await gv.precheck(_http(s), game, mods_mod._HashCache(), srv.cfg.java_bin, target,
                                     systemd=False, manage_unit=True, runner=_runner(s))  # fmt: skip
            prep = await gv.prepare(_http(s), _download(s), game, data_dir, plan, disable_unknown=False,
                                    java_mirror=body.java_mirror, runner=_runner(s), note=note)  # fmt: skip
            fs = appsetup.FreshSettings(body.motd, body.max_players, body.online_mode, body.white_list,
                                        body.difficulty, body.view_distance, body.port)  # fmt: skip
            pw = await asyncio.to_thread(
                appsetup.write_fresh_files, game, fs, srv.cfg.rcon_port, s.secrets_file
            )
            srv.rcon._password = pw  # 立即用新密码，不必重启 agent
            await asyncio.to_thread(gv.swap_in, game, prep, data_dir / "staging" / "fresh-swap")
            mem = f"{body.memory_gb}G"
            opts = [o for o in srv.cfg.java_opts if not o.startswith(("-Xms", "-Xmx"))] + [
                f"-Xms{mem}",
                f"-Xmx{mem}",
            ]
            changes: dict[str, Any] = {"java_opts": opts, "port": body.port}
            if prep.java_bin != srv.cfg.java_bin:
                changes["java_bin"] = prep.java_bin
            await s.config.save({"server": changes}, "setup", "首次安装")
            srv.cfg.jar, srv.cfg.java_bin, srv.cfg.java_opts, srv.cfg.port = (
                prep.jar,
                prep.java_bin,
                opts,
                body.port,
            )
            note("首次开服（会生成世界，可能需要一两分钟）")
            await srv.start()
            await s.ops.set_desired("running")
            from mcops.server import State

            st = await srv.wait_state(
                State.RUNNING, State.CRASHED, timeout=max(srv.cfg.start_ready_timeout_s, 600)
            )
            if st is not State.RUNNING:
                raise RuntimeError("首次开服没有就绪，请到「日志」查看服务端输出")
            note(f"安装完成：Minecraft {target.mc}，Fabric Loader {target.loader}")
            return {"mc": target.mc, "loader": target.loader, "jar": prep.jar}

        s.supervisor.run_once("job:setup", s.ops.jobs.run("setup_install", f"web:{sess.user}", job))
        await s.audit(sess.user, "setup.install", target=f"{body.mc}/{body.loader}", eula=True)
        return {"accepted": True}

    # ---- 自身升级 ----

    def _unit() -> str:
        return os.environ.get("MCOPS_SYSTEMD_UNIT", "mcops-agent.service")

    def _require_systemd() -> None:
        if not os.environ.get("INVOCATION_ID") or shutil.which("systemd-run") is None:
            raise HTTPException(
                409, "agent 不是由 systemd 启动的（开发模式），请用 deploy/push.sh 或手动安装"
            )

    @app.get("/api/system/updates")
    async def updates(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        cur = diagnostics.app_revision()
        return {"current": cur, "bundles": appsetup.list_bundles(cur)}

    @app.post("/api/system/updates/upload")
    async def update_upload(request: Request, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        chunks, total = [], 0
        async for c in request.stream():
            total += len(c)
            if total > appsetup.BUNDLE_MAX_BYTES:
                raise HTTPException(413, "安装包超过 300 MB")
            chunks.append(c)
        try:
            rev = await asyncio.to_thread(appsetup.unpack_bundle, b"".join(chunks))
        except gv.UpgradeError as e:
            raise HTTPException(400, str(e)) from None
        await s.audit(sess.user, "system.update.upload", target=rev)
        return {"revision": rev, "current": diagnostics.app_revision()}

    @app.post("/api/system/updates/install", status_code=202)
    async def update_install(body: InstallRevBody, sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        _require_systemd()
        if s.ops is not None and s.ops.jobs.running:
            raise HTTPException(409, f"任务 {s.ops.jobs.running} 正在运行，等它结束再升级 mc-ops")
        try:
            await appsetup.schedule_install(
                body.revision, _unit(), getattr(s, "update_runner", None)
            )
        except gv.UpgradeError as e:
            raise HTTPException(400, str(e)) from None
        await s.audit(sess.user, "system.update.install", target=body.revision)
        return {"accepted": True}
