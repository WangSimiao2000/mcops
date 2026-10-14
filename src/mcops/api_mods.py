"""API：Mod 与升级 页（M4）。在 create_app 里、SPA 兜底路由之前注册。"""

# 注意：本模块特意不启用 `from __future__ import annotations`。
# register() 在函数体内导入依赖别名（SessionDep/SvcDep），路由处理器用这些别名作
# Annotated 注解。若启用 PEP 563（字符串注解），FastAPI 会以模块级 get_type_hints
# 解析注解，解析不到 register() 的局部名字，把依赖参数误当查询参数（422）。
# 关闭 PEP 563 后注解在 def 时按闭包作用域即时求值，别名能正确解析，也避免 B008。

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field


class InstallRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    version_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class InstallBody(BaseModel):
    items: list[InstallRequest] = Field(min_length=1, max_length=20)


class MrpackApplyBody(BaseModel):
    token: str = Field(min_length=8, max_length=64)


class ConfigFileBody(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str
    mtime: float | None = None


def register(app: FastAPI) -> None:
    """在 create_app 里、SPA 兜底路由之前调用。"""
    from mcops import mods as mods_mod
    from mcops.api import Services, SessionDep, SvcDep
    from mcops.server import ConflictError

    observe_lock = asyncio.Lock()  # 没有 Ops（观测模式）时的兜底；观测模式本身也拒绝写操作

    hash_cache = mods_mod._HashCache()  # 跨请求复用，避免每次都重算 40+ 个 jar 的 sha512

    def _service(s: Services) -> mods_mod.ModsService:
        # 测试可在 Services 上挂 mods_http / mods_download 注入假 Modrinth
        extra: dict[str, Any] = {}
        if getattr(s, "mods_http", None) is not None:
            extra["http"] = s.mods_http  # type: ignore[attr-defined]
        if getattr(s, "mods_download", None) is not None:
            extra["download"] = s.mods_download  # type: ignore[attr-defined]
        return mods_mod.ModsService(
            engine=s.engine,
            game_dir=s.server.cfg.game_dir,
            jar_glob=s.server.cfg.jar,
            hashes=hash_cache,
            **extra,
        )

    def _server_started_at(s: Services) -> datetime | None:
        """把 ServerController.started_at（time.monotonic 轴）换算成墙钟时间。

        用于判断 mods 改动后服务端是否已重启过（pending_restart 自动失效）。
        monotonic 不是墙钟，这里按"现在 - 已运行秒数"反推出近似启动墙钟时刻。
        """
        import time

        started = s.server.started_at
        if started is None:
            return None
        age_s = max(time.monotonic() - started, 0.0)
        return datetime.now(UTC) - timedelta(seconds=age_s)

    def _deny_observe(s: Services) -> None:
        if s.server.observe:
            raise HTTPException(409, "观测模式：mod 的启停与升级暂由手动操作，面板不接管")

    @contextlib.asynccontextmanager
    async def _exclusive(s: Services, op: str) -> AsyncIterator[None]:
        """mods/ 的写操作彼此串行，并与定时重启等运维任务互斥，避免服务端加载半更新的目录。"""
        _deny_observe(s)
        async with contextlib.AsyncExitStack() as stack:
            if s.ops is None:
                await stack.enter_async_context(observe_lock)
            else:
                try:
                    await stack.enter_async_context(s.ops.jobs.exclusive(f"mod_{op}"))
                except ConflictError as e:
                    raise HTTPException(409, str(e)) from None
            yield

    @app.get("/api/mods")
    async def mods_overview(sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        return await _service(s).overview(_server_started_at(s))

    @app.post("/api/mods/check-updates")
    async def mods_check_updates(sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        result = await _service(s).refresh_updates()
        n = sum(
            1
            for v in result.get("updates", {}).values()
            if isinstance(v, dict) and v.get("has_update")
        )
        await s.audit(sess.user, "mods.check_updates", updates=n, error=result.get("error"))
        return result

    @app.post("/api/mods/{file}/disable")
    async def mods_disable(file: str, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        async with _exclusive(s, "disable"):
            try:
                r = await mods_mod.disable_mod(s.engine, _service(s).game_dir, file)
            except mods_mod.ModError as e:
                await s.audit(sess.user, "mods.disable", target=file, result="error", detail=str(e))
                raise HTTPException(400, str(e)) from None
            await s.audit(sess.user, "mods.disable", target=file)
            return r

    @app.post("/api/mods/{file}/enable")
    async def mods_enable(file: str, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        async with _exclusive(s, "enable"):
            try:
                r = await mods_mod.enable_mod(s.engine, _service(s).game_dir, file)
            except mods_mod.ModError as e:
                await s.audit(sess.user, "mods.enable", target=file, result="error", detail=str(e))
                raise HTTPException(400, str(e)) from None
            await s.audit(sess.user, "mods.enable", target=file)
            return r

    @app.post("/api/mods/{file}/update")
    async def mods_update(file: str, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        async with _exclusive(s, "update"):
            svc_mods = _service(s)
            cached = await mods_mod.get_cached_updates(s.engine)
            if cached is None:
                raise HTTPException(409, "尚无更新信息，请先点击「检查更新」")
            try:
                r = await mods_mod.update_mod(
                    s.engine, svc_mods.game_dir, file, cached, svc_mods.download
                )
            except mods_mod.ModError as e:
                await s.audit(sess.user, "mods.update", target=file, result="error", detail=str(e))
                raise HTTPException(400, str(e)) from None
            await s.audit(sess.user, "mods.update", target=file, **r)
            return r

    @app.post("/api/mods/update-all")
    async def mods_update_all(sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        async with _exclusive(s, "update_all"):
            results = await _service(s).update_all()
            ok = sum(1 for r in results if r.get("ok"))
            await s.audit(sess.user, "mods.update_all", total=len(results), ok=ok)
            return {"results": results}

    @app.post("/api/mods/{file}/rollback")
    async def mods_rollback(file: str, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        async with _exclusive(s, "rollback"):
            try:
                r = await mods_mod.rollback_mod(s.engine, _service(s).game_dir, file)
            except mods_mod.ModError as e:
                await s.audit(
                    sess.user, "mods.rollback", target=file, result="error", detail=str(e)
                )
                raise HTTPException(400, str(e)) from None
            await s.audit(sess.user, "mods.rollback", target=file, **r)
            return r

    # ---- P2：一键安装 ---------------------------------------------------------
    from mcops import modstore

    async def _read_body(request: Request, limit: int) -> bytes:
        chunks, total = [], 0
        async for c in request.stream():
            total += len(c)
            if total > limit:
                raise HTTPException(413, f"文件超过 {limit // 2**20} MB")
            chunks.append(c)
        if not total:
            raise HTTPException(422, "文件为空")
        return b"".join(chunks)

    def _mc(s: Services) -> tuple[str | None, str | None]:
        return _service(s).mc_and_loader()

    async def _modrinth(coro: Any) -> Any:
        try:
            return await coro
        except mods_mod.ModError as e:
            raise HTTPException(400, str(e)) from None
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"访问 Modrinth 失败：{type(e).__name__}: {e}") from None

    @app.get("/api/mods/search")
    async def mods_search(
        _: SessionDep,
        s: SvcDep,
        q: Annotated[str, Query(max_length=100)] = "",
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        index: Annotated[
            str, Query(pattern="^(relevance|downloads|follows|newest|updated)$")
        ] = "relevance",
        any_version: bool = False,
    ) -> dict[str, Any]:
        mc, _l = _mc(s)
        svc_mods = _service(s)
        res = await _modrinth(
            modstore.search(svc_mods.http, q, None if any_version else mc, offset, limit, index)
        )
        installed = await modstore.installed_projects(
            svc_mods.http, svc_mods.game_dir, svc_mods.hashes
        )
        for h in res["hits"]:
            h["installed_file"] = installed.get(str(h["project_id"]))
        res["mc_version"] = mc
        return res  # type: ignore[no-any-return]

    @app.get("/api/mods/project/{project}/versions")
    async def mods_versions(
        project: str, _: SessionDep, s: SvcDep, all_versions: bool = False
    ) -> dict[str, Any]:
        mc, _l = _mc(s)
        items = await _modrinth(
            modstore.project_versions(_service(s).http, project, None if all_versions else mc)
        )
        return {"items": items, "mc_version": mc}

    @app.post("/api/mods/install/plan")
    async def mods_install_plan(body: InstallBody, _: SessionDep, s: SvcDep) -> dict[str, Any]:
        sm = _service(s)
        mc, _l = _mc(s)
        reqs = [r.model_dump() for r in body.items]
        return await _modrinth(modstore.plan_install(sm.http, sm.game_dir, sm.hashes, mc, reqs))  # type: ignore[no-any-return]

    @app.post("/api/mods/install")
    async def mods_install(body: InstallBody, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        sm = _service(s)
        mc, _l = _mc(s)
        async with _exclusive(s, "install"):
            # 服务端重新解析一遍，不信任客户端给的下载地址
            plan = await _modrinth(
                modstore.plan_install(
                    sm.http, sm.game_dir, sm.hashes, mc, [r.model_dump() for r in body.items]
                )
            )
            try:
                r = await modstore.install_items(s.engine, sm.game_dir, sm.download, plan["items"])
            except mods_mod.ModError as e:
                await s.audit(sess.user, "mods.install", result="error", detail=str(e))
                raise HTTPException(400, str(e)) from None
            await s.audit(sess.user, "mods.install", files=r["installed"])
            return {**r, "plan": plan}

    @app.post("/api/mods/upload")
    async def mods_upload(
        request: Request,
        sess: SessionDep,
        s: SvcDep,
        filename: Annotated[str, Query(max_length=200)],
    ) -> dict[str, Any]:
        sm = _service(s)
        mc, _l = _mc(s)
        async with _exclusive(s, "upload"):
            data = await _read_body(request, mods_mod.MAX_DOWNLOAD_BYTES)
            try:
                r = await modstore.upload_jar(s.engine, sm.game_dir, sm.hashes, filename, data, mc)
            except mods_mod.ModError as e:
                await s.audit(
                    sess.user, "mods.upload", target=filename, result="error", detail=str(e)
                )
                raise HTTPException(400, str(e)) from None
            await s.audit(sess.user, "mods.upload", target=r["file"], mod_id=r["id"])
            return r

    mrpack_cache: dict[str, tuple[float, bytes, dict[str, Any]]] = {}

    @app.post("/api/mods/mrpack/plan")
    async def mrpack_plan(request: Request, _: SessionDep, s: SvcDep) -> dict[str, Any]:
        import secrets as _secrets
        import time

        data = await _read_body(request, modstore.MRPACK_MAX_BYTES)
        mc, loader = _mc(s)
        try:
            plan = await asyncio.to_thread(modstore.read_mrpack, data, mc, loader)
        except mods_mod.ModError as e:
            raise HTTPException(400, str(e)) from None
        now = time.monotonic()
        for k in [k for k, v in mrpack_cache.items() if now - v[0] > 1800]:
            mrpack_cache.pop(k, None)
        mrpack_cache.clear() if len(mrpack_cache) > 3 else None  # 只留最近几个，避免占内存
        token = _secrets.token_urlsafe(16)
        mrpack_cache[token] = (now, data, plan)
        return {**plan, "token": token}

    @app.post("/api/mods/mrpack/apply")
    async def mrpack_apply(body: MrpackApplyBody, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        entry = mrpack_cache.pop(body.token, None)
        if entry is None:
            raise HTTPException(409, "整合包预览已过期，请重新上传")
        _t, data, plan = entry
        sm = _service(s)
        async with _exclusive(s, "mrpack"):
            try:
                r = await modstore.apply_mrpack(s.engine, sm.game_dir, sm.download, data, plan)
            except mods_mod.ModError as e:
                await s.audit(sess.user, "mods.mrpack", result="error", detail=str(e))
                raise HTTPException(400, str(e)) from None
            await s.audit(
                sess.user, "mods.mrpack", target=str(plan.get("name")), written=len(r["written"])
            )
            return r

    # ---- config/ 在线编辑 ----

    def _config_backup_dir(s: Services) -> Any:
        return s.server.cfg.game_dir / ".mcops-backup"

    @app.get("/api/mods/config-files")
    async def config_files(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return {"items": await asyncio.to_thread(modstore.list_config_files, s.server.cfg.game_dir)}

    @app.get("/api/mods/config-file")
    async def config_file(
        _: SessionDep, s: SvcDep, path: Annotated[str, Query(max_length=300)]
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(modstore.read_config_file, s.server.cfg.game_dir, path)
        except mods_mod.ModError as e:
            raise HTTPException(400, str(e)) from None

    @app.put("/api/mods/config-file")
    async def config_file_save(body: ConfigFileBody, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        async with _exclusive(s, "config_edit"):
            try:
                r = await asyncio.to_thread(
                    modstore.write_config_file, s.server.cfg.game_dir, _config_backup_dir(s),
                    body.path, body.content, body.mtime,
                )  # fmt: skip
            except mods_mod.ModError as e:
                raise HTTPException(400, str(e)) from None
            await mods_mod._record_pending(s.engine, {"action": "config", "file": body.path})
            await s.audit(sess.user, "mods.config_edit", target=body.path)
            return r
