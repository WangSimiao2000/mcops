"""API：网络 页（M4）。在 create_app 里、SPA 兜底路由之前注册。"""

# 注意：本模块特意不启用 `from __future__ import annotations`（同 api_mods.py 的原因）：
# register() 在函数体内导入依赖别名（SessionDep/SvcDep），路由处理器用这些别名作
# Annotated 注解；启用 PEP 563 会让 FastAPI 以模块级 get_type_hints 解析不到局部名字。

import ipaddress
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


class BanBody(BaseModel):
    ip: str = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=200)


class ScannerBanBody(BaseModel):
    window: str = Field(default="24h", max_length=8)
    ips: list[str] | None = Field(default=None, max_length=50)


def register(app: FastAPI) -> None:
    """在 create_app 里、SPA 兜底路由之前调用。"""
    from mcops import network as net
    from mcops.api import Services, SessionDep, SvcDep
    from mcops.events import Event

    def _banned(s: Services) -> set[str]:
        return s.files.banned_ips() if s.files else set()

    def _game_dir(s: Services) -> Path:
        return s.server.cfg.game_dir

    def _deny_observe(s: Services) -> None:
        if s.server.observe:
            raise HTTPException(409, "观测模式：封禁操作仍由手动执行，面板不接管")

    def _net_error(e: net.NetworkError) -> HTTPException:
        return HTTPException(e.status, e.detail)

    async def _ban_one(s: Services, sess_user: str, ip: str, reason: str | None) -> dict[str, Any]:
        """校验 + RCON ban-ip + 发事件 + 写审计。返回单个结果。"""
        try:
            clean = net.validate_ban_ip(ip, s.caps.proxy_sources if s.caps else ())
        except net.NetworkError as e:
            await s.audit(sess_user, "network.ban", target=ip, result="error", detail=e.detail)
            raise _net_error(e) from None
        reason = net.sanitize_ban_reason(reason)
        cmd = f"ban-ip {clean}" + (f" {reason}" if reason else "")
        out = await s.server.rcon.command(cmd)
        await s.bus.emit(
            Event("ip_banned", "network", {"ip": clean, "reason": reason, "by": sess_user})
        )
        await s.audit(sess_user, "network.ban", target=clean, reason=reason or "")
        return {"ip": clean, "ok": True, "output": out}

    # ---- 概览 / 统计 / 列表 / 流水 ----

    @app.get("/api/network/overview")
    async def network_overview(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return await net.overview(s)

    @app.get("/api/network/stats")
    async def network_stats(_: SessionDep, s: SvcDep, window: str = "24h") -> dict[str, Any]:
        try:
            return await net.stats(s.engine, window)
        except net.NetworkError as e:
            raise _net_error(e) from None

    @app.get("/api/network/ips")
    async def network_ips(
        _: SessionDep,
        s: SvcDep,
        window: str = "24h",
        q: str | None = None,
        tag: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        try:
            return await net.ip_list(
                s.engine, _banned(s), window=window, q=q, tag=tag, limit=limit, offset=offset
            )
        except net.NetworkError as e:
            raise _net_error(e) from None

    @app.get("/api/network/recent")
    async def network_recent(
        _: SessionDep,
        s: SvcDep,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> dict[str, Any]:
        return await net.recent(s.engine, _banned(s), limit)

    @app.get("/api/network/ssh")
    async def network_ssh(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return await net.ssh_sources(s.engine, 24)

    # ---- 封禁管理 ----

    @app.get("/api/network/bans")
    async def network_bans(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return {"items": net.list_bans(_game_dir(s))}

    @app.post("/api/network/bans")
    async def network_ban(body: BanBody, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        _deny_observe(s)
        return await _ban_one(s, sess.user, body.ip, body.reason)

    @app.post("/api/network/bans/{ip}/pardon")
    async def network_pardon(ip: str, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        _deny_observe(s)
        try:
            clean = str(ipaddress.ip_address(ip))
        except ValueError:
            await s.audit(sess.user, "network.pardon", target=ip, result="error")
            raise HTTPException(422, f"不是合法的 IP 地址：{ip}") from None
        out = await s.server.rcon.command(f"pardon-ip {clean}")
        await s.bus.emit(Event("ip_unbanned", "network", {"ip": clean, "by": sess.user}))
        await s.audit(sess.user, "network.pardon", target=clean)
        return {"ip": clean, "ok": True, "output": out}

    # ---- 扫描器 ----

    @app.get("/api/network/scanners")
    async def network_scanners(_: SessionDep, s: SvcDep, window: str = "24h") -> dict[str, Any]:
        try:
            return await net.scanners(s.engine, window)
        except net.NetworkError as e:
            raise _net_error(e) from None

    @app.post("/api/network/scanners/ban")
    async def network_scanners_ban(
        body: ScannerBanBody, sess: SessionDep, s: SvcDep
    ) -> dict[str, Any]:
        _deny_observe(s)
        if body.ips is not None:
            candidates = list(dict.fromkeys(body.ips))  # 去重保序
        else:
            try:
                preview = await net.scanners(s.engine, body.window)
            except net.NetworkError as e:
                raise _net_error(e) from None
            candidates = [it["ip"] for it in preview["items"]]
        if len(candidates) > 50:
            raise HTTPException(422, "单次最多封禁 50 个 IP")
        results: list[dict[str, Any]] = []
        for ip in candidates:
            try:
                results.append(await _ban_one(s, sess.user, ip, "scanner"))
            except HTTPException as e:
                results.append({"ip": ip, "ok": False, "error": str(e.detail)})
        ok = sum(1 for r in results if r.get("ok"))
        await s.audit(sess.user, "network.scanners_ban", total=len(results), ok=ok)
        return {"total": len(results), "ok": ok, "results": results}
