"""API：游戏版本安装/升级与 Java（P3）。"""

# 注意：本模块特意不启用 `from __future__ import annotations`（同 api_mods.py 的原因）。

import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

VER = r"^[0-9A-Za-z.+_-]{1,40}$"


class TargetBody(BaseModel):
    mc: str = Field(pattern=VER)
    loader: str = Field(pattern=VER)
    installer: str | None = Field(default=None, pattern=VER)


class PrepareBody(TargetBody):
    disable_unknown: bool = False
    java_mirror: str = Field(default="tuna", pattern="^(tuna|official)$")


PREPARED_MAX_AGE_S = 24 * 3600


def register(app: FastAPI) -> None:
    from mcops import gameversions as gv
    from mcops import mods as mods_mod
    from mcops.api import Services, SessionDep, SvcDep
    from mcops.server import ConflictError

    state: dict[str, Any] = {
        "prepared": None,
        "versions": None,
        "versions_at": 0.0,
        "prepare_error": None,
    }
    hashes = mods_mod._HashCache()

    def _http(s: Services) -> Any:
        return getattr(s, "mods_http", None) or mods_mod._default_http

    def _download(s: Services) -> Any:
        return getattr(s, "mods_download", None) or mods_mod._default_download

    def _runner(s: Services) -> Any:
        return getattr(s, "game_runner", None) or gv._default_runner

    def _ops(s: Services) -> Any:
        if s.ops is None:
            raise HTTPException(409, "观测模式下不能安装或升级服务端")
        return s.ops

    async def _target(s: Services, b: TargetBody) -> gv.Target:
        installer = b.installer
        if installer is None:
            v = await _versions(s)
            installer = v["installer"]
        if not installer:
            raise HTTPException(502, "读不到 Fabric 启动器版本")
        return gv.Target(b.mc, b.loader, installer)

    async def _versions(s: Services) -> dict[str, Any]:
        if state["versions"] is None or time.monotonic() - state["versions_at"] > 600:
            try:
                state["versions"] = await gv.list_versions(_http(s))
            except Exception as e:
                raise HTTPException(
                    502, f"访问 Fabric Meta 失败：{type(e).__name__}: {e}"
                ) from None
            state["versions_at"] = time.monotonic()
        return state["versions"]  # type: ignore[no-any-return]

    def _cfg_flags(s: Services) -> tuple[bool, bool]:
        c = s.server.cfg
        return c.backend == "systemd", c.manage_unit

    def _data_dir(s: Services) -> Any:
        if s.config is not None:
            return s.config.running.data_dir
        raise HTTPException(503, "config_store_unavailable")

    async def _plan(s: Services, b: TargetBody) -> dict[str, Any]:
        systemd, managed = _cfg_flags(s)
        t = await _target(s, b)
        try:
            return await gv.precheck(
                _http(s), s.server.cfg.game_dir, hashes, s.server.cfg.java_bin, t,
                systemd=systemd, manage_unit=managed, runner=_runner(s),
            )  # fmt: skip
        except gv.UpgradeError as e:
            raise HTTPException(400, str(e)) from None
        except mods_mod.ModError as e:
            raise HTTPException(400, str(e)) from None
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"预检时访问外部服务失败：{type(e).__name__}: {e}") from None

    @app.get("/api/game/versions")
    async def game_versions(_: SessionDep, s: SvcDep, snapshots: bool = False) -> dict[str, Any]:
        v = await _versions(s)
        cur = gv.current_jar(s.server.cfg.game_dir)
        mc, loader = mods_mod.parse_server_jar(cur) if cur else (None, None)
        games = v["games"] if snapshots else [g for g in v["games"] if g["stable"]]
        java = await gv.java_major(s.server.cfg.java_bin, _runner(s))
        prep = state["prepared"]
        return {
            "current": {"jar": cur, "mc": mc, "loader": loader, "java_bin": s.server.cfg.java_bin, "java_major": java},
            "games": games[:60],
            "loaders": [x for x in v["loaders"] if x["stable"]][:20],
            "installer": v["installer"],
            "managed_javas": gv.managed_javas(_data_dir(s)),
            "prepared": prep.to_json() if prep is not None else None,
            "prepare_error": state["prepare_error"],
            "unit_managed": s.server.cfg.manage_unit or s.server.cfg.backend != "systemd",
        }  # fmt: skip

    @app.post("/api/game/precheck")
    async def game_precheck(body: TargetBody, _: SessionDep, s: SvcDep) -> dict[str, Any]:
        return await _plan(s, body)

    @app.post("/api/game/prepare", status_code=202)
    async def game_prepare(body: PrepareBody, sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        ops = _ops(s)
        plan = await _plan(s, body)
        if plan["blockers"]:
            raise HTTPException(409, "预检未通过：" + "；".join(plan["blockers"]))
        state["prepared"], state["prepare_error"] = None, None

        async def job(note: Any) -> dict[str, Any]:
            try:
                prep = await gv.prepare(
                    _http(s), _download(s), s.server.cfg.game_dir, _data_dir(s), plan,
                    disable_unknown=body.disable_unknown, java_mirror=body.java_mirror,
                    runner=_runner(s), note=note,
                )  # fmt: skip
            except Exception as e:
                state["prepare_error"] = str(e)
                raise
            state["prepared"] = prep
            return prep.to_json()

        if ops.jobs.running:
            raise HTTPException(409, f"任务 {ops.jobs.running} 正在运行")
        s.supervisor.run_once(
            "job:game_prepare", ops.jobs.run("game_prepare", f"web:{sess.user}", job)
        )
        await s.audit(
            sess.user, "game.prepare", target=f"{plan['target']['mc']}/{plan['target']['loader']}"
        )
        return {"accepted": True}

    @app.post("/api/game/upgrade", status_code=202)
    async def game_upgrade(body: TargetBody, sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        ops = _ops(s)
        prep = state["prepared"]
        if prep is None:
            raise HTTPException(409, "请先完成「准备」（下载并试启动新版本）")
        if (prep.target.mc, prep.target.loader) != (body.mc, body.loader):
            raise HTTPException(409, "已准备的版本与要升级的不一致，请重新准备")
        if time.time() - prep.prepared_at > PREPARED_MAX_AGE_S:
            state["prepared"] = None
            raise HTTPException(409, "准备结果超过 24 小时，请重新准备")
        if ops.jobs.running:
            raise HTTPException(409, f"任务 {ops.jobs.running} 正在运行")

        async def save_java(java_bin: str) -> None:
            if s.config is not None:
                await s.config.save(
                    {"server": {"java_bin": java_bin}}, "game-upgrade", "升级时切换 Java"
                )

        async def job(note: Any) -> dict[str, Any]:
            try:
                return await ops.game_upgrade(prep, save_java)(note)  # type: ignore[no-any-return]
            finally:
                state["prepared"] = None  # 暂存文件已移走（或回滚时丢弃），必须重新准备

        try:
            s.supervisor.run_once(
                "job:game_upgrade", ops.jobs.run("game_upgrade", f"web:{sess.user}", job)
            )
        except ConflictError as e:
            raise HTTPException(409, str(e)) from None
        await s.audit(sess.user, "game.upgrade", target=f"{body.mc}/{body.loader}")
        return {"accepted": True}
