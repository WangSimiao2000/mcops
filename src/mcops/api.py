"""FastAPI 应用：REST + WebSocket。契约见 web/README.md。"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import Scope
from starlette.websockets import WebSocketDisconnect

from mcops import __version__, db
from mcops import players as pl
from mcops import security as sec
from mcops.auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    Authenticator,
    LoginError,
    Session,
    csrf_ok,
)
from mcops.bot.texts import mask_openid
from mcops.config import ApiConfig
from mcops.edge import EdgeCollector
from mcops.events import Broadcaster, Event, EventBus
from mcops.players import ServerFiles
from mcops.server import ConflictError, ServerController
from mcops.supervisor import Supervisor

log = logging.getLogger(__name__)


class RestoreBody(BaseModel):
    repo: str = Field(default="local", pattern=r"^(local|nas|s3)$")
    scope: str = Field(default="world", pattern=r"^(world|game)$")


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


class Services:
    """agent 运行期依赖的集合，由 main 组装后挂到 app.state.svc。"""

    def __init__(
        self,
        api_cfg: ApiConfig,
        engine: AsyncEngine,
        bus: EventBus,
        server: ServerController,
        auth: Authenticator,
        supervisor: Supervisor,
        files: ServerFiles | None = None,
        edge: EdgeCollector | None = None,
        mode: str = "control",
    ) -> None:
        self.files = files
        self.bot: Any = None  # BotRuntime（main 组装）
        self.ops: Any = None  # Ops（control 模式）
        self.disabled_rules: set[str] = set()
        self.unavailable_rules: dict[str, str] = {}
        """当前环境收不到信号、被强制停用的规则 → 原因。"""
        self.notify: Any = None  # NotifyHub
        self.config: Any = None  # ConfigStore（P1 面板配置）
        self.caps: Any = None  # Capabilities
        self.host: Any = None  # HostStats（主机资源采样）
        self.secrets_file: Any = None  # Path，面板改密 / 机密写回
        self.download_rate_kib: int = 512  # 备份下载限速（ops.download_rate_kib）
        self.edge = edge
        self.mode = mode
        self.alerts: Broadcaster[dict[str, Any]] = Broadcaster()
        self.api_cfg = api_cfg
        self.engine = engine
        self.bus = bus
        self.server = server
        self.auth = auth
        self.supervisor = supervisor

    async def audit(
        self, actor: str, action: str, target: str = "", result: str = "ok", **detail: Any
    ) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(db.audit_log).values(
                    ts=datetime.now(UTC),
                    actor=actor,
                    channel="web",
                    action=action,
                    target=target,
                    result=result,
                    detail=detail,
                )
            )


# ---- 请求来源校验（防 DNS rebinding / 跨站请求） ----------------------------


def _host_ok(host_header: str | None, cfg: ApiConfig) -> bool:
    if not host_header:
        return False
    host = urlsplit(f"//{host_header}").hostname or ""
    return host in _LOOPBACK_HOSTS or host_header in {
        urlsplit(o).netloc for o in cfg.allowed_origins
    }


def _origin_ok(origin: str | None, host_header: str | None, cfg: ApiConfig) -> bool:
    if origin is None:
        return True  # 非浏览器客户端（curl、CLI）不带 Origin；仍需会话 + CSRF
    if origin in cfg.allowed_origins:
        return True
    return urlsplit(origin).netloc == host_header


# ---- 依赖 ----------------------------------------------------------------


def svc(request: Request) -> Services:
    s: Services = request.app.state.svc
    return s


SvcDep = Annotated[Services, Depends(svc)]


async def require_session(request: Request, s: SvcDep) -> Session:
    sess = await s.auth.get_session(request.cookies.get(SESSION_COOKIE))
    if sess is None:
        raise HTTPException(401, "not_authenticated")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not csrf_ok(
        sess, request.headers.get(CSRF_HEADER)
    ):
        raise HTTPException(403, "csrf_failed")
    return sess


SessionDep = Annotated[Session, Depends(require_session)]


# ---- 模型 ----------------------------------------------------------------


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    totp: str | None = Field(default=None, max_length=12)


class AckAllBody(BaseModel):
    severity: str | None = Field(default=None, pattern="^(critical|warning|info)$")


class SimulateBody(BaseModel):
    content: str = Field(max_length=500)
    as_admin: bool = False


class WhitelistAddBody(BaseModel):
    name: str = Field(max_length=16)


class CommandBody(BaseModel):
    command: str = Field(min_length=1, max_length=1000)


def create_app(services_factory: Callable[[], Awaitable[Services]] | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if services_factory is not None:
            app.state.svc = await services_factory()
        try:
            yield
        finally:
            svc = getattr(app.state, "svc", None)
            if svc is not None:
                log.info("agent 关停：收尾后台任务")
                await svc.supervisor.stop_all()

    app = FastAPI(title="mc-ops", version=__version__, lifespan=lifespan, docs_url=None)

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cfg: ApiConfig = request.app.state.svc.api_cfg
        host = request.headers.get("host")
        if not _host_ok(host, cfg):
            return JSONResponse({"detail": "bad_host"}, status_code=421)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _origin_ok(
            request.headers.get("origin"), host, cfg
        ):
            return JSONResponse({"detail": "bad_origin"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    # ---- 认证 ----

    @app.post("/api/auth/login")
    async def login(
        body: LoginBody, request: Request, response: Response, s: SvcDep
    ) -> dict[str, str]:
        client = request.client.host if request.client else "unknown"
        delay = s.auth.limiter.delay_s()
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            s.auth.verify(body.password, body.totp)
        except LoginError as e:
            if e.code != "totp_required":
                s.auth.limiter.failure()
            await s.audit("anonymous", "login", result=e.code, client=client)
            raise HTTPException(401, e.code) from None
        s.auth.limiter.success()
        token, sess = await s.auth.create_session(client)
        ttl = s.api_cfg.session_ttl_s
        secure = s.api_cfg.cookie_secure
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=ttl,
            httponly=True,
            samesite="strict",
            secure=secure,
            path="/",
        )
        response.set_cookie(
            CSRF_COOKIE,
            sess.csrf,
            max_age=ttl,
            httponly=False,
            samesite="strict",
            secure=secure,
            path="/",
        )
        await s.audit(sess.user, "login", client=client)
        return {"user": sess.user}

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request, response: Response, sess: SessionDep, s: SvcDep) -> None:
        await s.auth.delete_session(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        await s.audit(sess.user, "logout")

    @app.get("/api/auth/me")
    async def me(sess: SessionDep) -> dict[str, str]:
        return {"user": sess.user}

    # ---- 状态 ----

    @app.get("/api/health")
    async def health(s: SvcDep) -> dict[str, str]:
        degraded = any(m.state == "failed" for m in s.supervisor.modules.values())
        return {"status": "degraded" if degraded else "ok", "version": __version__}

    @app.get("/api/system")
    async def system(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return {
            "mode": s.mode,
            "modules": {k: v.to_json() for k, v in s.supervisor.modules.items()},
        }

    @app.get("/api/host/stats")
    async def host_stats(_: SessionDep, s: SvcDep, history: bool = True) -> dict[str, Any]:
        if s.host is None:
            raise HTTPException(503, "host_stats_unavailable")
        if s.host.current is None:
            await s.host.sample()
        return s.host.to_json(history)  # type: ignore[no-any-return]

    @app.get("/api/server/status")
    async def server_status(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return s.server.status_snapshot()

    async def _op(
        s: Services, sess: Session, name: str, fn: Callable[[], Awaitable[None]]
    ) -> JSONResponse:
        try:
            await fn()
        except ConflictError as e:
            await s.audit(sess.user, f"server.{name}", result="conflict", detail=str(e))
            raise HTTPException(409, str(e)) from None
        await s.audit(sess.user, f"server.{name}")
        return JSONResponse({"accepted": True}, status_code=202)

    @app.post("/api/server/start")
    async def server_start(sess: SessionDep, s: SvcDep) -> JSONResponse:
        r = await _op(s, sess, "start", s.server.start)
        if s.ops is not None:
            s.ops.reset_watchdog()
            await s.ops.set_desired("running")
        return r

    @app.post("/api/server/stop")
    async def server_stop(sess: SessionDep, s: SvcDep) -> JSONResponse:
        r = await _op(s, sess, "stop", s.server.stop)
        if s.ops is not None:
            await s.ops.set_desired("stopped")  # 手动关服后，agent 重启不会自动拉起
        return r

    @app.post("/api/server/restart")
    async def server_restart(sess: SessionDep, s: SvcDep) -> JSONResponse:
        async def _bg() -> None:
            s.server.deny_if_observe()
            if s.server.state.value not in ("running", "starting"):
                raise ConflictError(f"当前状态为 {s.server.state}，不能重启")
            s.supervisor.run_once("server_restart", s.server.restart())

        return await _op(s, sess, "restart", _bg)

    @app.post("/api/server/command")
    async def server_command(body: CommandBody, sess: SessionDep, s: SvcDep) -> dict[str, str]:
        try:
            out = await s.server.command(body.command)
        except ConflictError as e:
            raise HTTPException(409, str(e)) from None
        await s.audit(sess.user, "server.command", target=body.command)
        return {"output": out}

    @app.get("/api/events")
    async def events(
        _: SessionDep,
        s: SvcDep,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        types: str | None = None,
    ) -> dict[str, Any]:
        ts = [t for t in (types or "").split(",") if t]
        return {"items": [e.to_json() for e in await s.bus.recent(limit, ts or None)]}

    # ---- 玩家 / 会话 / IP ----

    def _files(s: Services) -> ServerFiles:
        if s.files is None:
            raise HTTPException(503, "server_files_unavailable")
        return s.files

    @app.get("/api/players")
    async def players(
        _: SessionDep,
        s: SvcDep,
        q: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> dict[str, Any]:
        return {"items": await pl.list_players(s.engine, _files(s), q, limit)}

    @app.get("/api/players/{name}")
    async def player(name: str, _: SessionDep, s: SvcDep) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
            raise HTTPException(400, "invalid_name")
        found = [
            p
            for p in await pl.list_players(s.engine, _files(s), name, 1000)
            if p["name"].lower() == name.lower()
        ]
        if not found:
            raise HTTPException(404, "player_not_found")
        sessions, ips = await pl.player_detail(s.engine, name)
        evs = [e.to_json() for e in await s.bus.recent_for_name(name, 100)]
        return {"player": found[0], "sessions": sessions, "ips": ips, "events": evs}

    @app.get("/api/sessions")
    async def sessions(
        _: SessionDep,
        s: SvcDep,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        online: bool | None = None,
    ) -> dict[str, Any]:
        return {"items": await pl.recent_sessions(s.engine, limit, online)}

    @app.get("/api/ips/{ip}")
    async def ip_detail(ip: str, _: SessionDep, s: SvcDep) -> dict[str, Any]:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise HTTPException(400, "invalid_ip") from None
        banned = s.files.banned_ips() if s.files else set()
        return await pl.ip_info(s.engine, ip, banned)

    # ---- 安全 ----

    @app.get("/api/alerts")
    async def alerts(
        _: SessionDep,
        s: SvcDep,
        status: str = "open",
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        items, counts = await sec.list_alerts(s.engine, status, limit)
        return {"items": items, "open_counts": counts}

    @app.post("/api/alerts/{alert_id}/ack")
    async def ack(alert_id: int, sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        n = await sec.ack_alerts(s.engine, alert_id=alert_id)
        await s.audit(sess.user, "alert.ack", target=str(alert_id))
        return {"ok": n > 0}

    @app.post("/api/alerts/ack-all")
    async def ack_all(body: AckAllBody, sess: SessionDep, s: SvcDep) -> dict[str, int]:
        n = await sec.ack_alerts(s.engine, severity=body.severity)
        await s.audit(sess.user, "alert.ack_all", target=body.severity or "", count=n)
        return {"acked": n}

    @app.get("/api/security/rules")
    async def rules(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        disabled = s.disabled_rules
        return {
            "items": [
                {
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "severity": r.severity,
                    "enabled": r.id not in disabled,
                    "unavailable": s.unavailable_rules.get(r.id),
                }
                for r in sec.RULES.values()
            ]
        }

    @app.get("/api/edge/status")
    async def edge_status(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        if s.edge is None:
            return {
                "enabled": False,
                "connected": False,
                "last_event_ts": None,
                "error": None,
                "events_24h": 0,
            }
        st = s.edge.status()
        st["events_24h"] = await s.edge.count_since(datetime.now(UTC) - timedelta(hours=24))
        return st

    # ---- 运维任务 / 备份 ----

    def _ops(s: Services) -> Any:
        if s.ops is None:
            raise HTTPException(409, "观测模式：定时任务与备份仍由旧脚本负责")
        return s.ops

    @app.get("/api/jobs")
    async def jobs(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        o = _ops(s)
        nxt = o.next_restart()
        return {
            "jobs": [
                {
                    "id": "scheduled_restart",
                    "name": "定时重启 + 冷备份",
                    "schedule": o.cfg.restart_time,
                    "next_run": nxt.isoformat() if nxt else None,
                },
                {"id": "backup", "name": "立即备份（不停服）", "schedule": None, "next_run": None},
                *(
                    [
                        {
                            "id": "restic_backup",
                            "name": "增量备份（restic）",
                            "schedule": f"每 {o.cfg.restic_interval_minutes} 分钟",
                            "next_run": o.restic_next.isoformat() if o.restic_next else None,
                        }
                    ]
                    if o.restic is not None
                    else []
                ),
                *(
                    [
                        {
                            "id": "offsite_copy",
                            "name": "异地备份（复制增量快照）",
                            "schedule": "每次增量备份之后",
                            "next_run": None,
                        }
                    ]
                    if o.restic is not None and o.offsite_targets is not None
                    else []
                ),
            ],
            "running": o.jobs.running or o.offsite_jobs.running,
            # 正在跑的任务的实时日志与进度（运行记录要等结束才落库）
            "live": o.jobs.live_state() or o.offsite_jobs.live_state(),
            "watchdog": {"suspended": o.autorestart_suspended, "recent_crashes": len(o._crashes)},
            "history": await o.jobs.history(50),
        }

    @app.post("/api/jobs/{job}/run", status_code=202)
    async def job_run(job: str, sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        o = _ops(s)
        fns = {"scheduled_restart": o.scheduled_restart, "backup": o.manual_backup}
        if o.restic is not None:
            fns["restic_backup"] = o.restic_backup
        if o.restic is not None and o.offsite_targets is not None:
            fns["offsite_copy"] = o.offsite_copy
        if job not in fns:
            raise HTTPException(404, "unknown_job")
        runner = o.offsite_jobs if job == "offsite_copy" else o.jobs
        if runner.running:
            raise HTTPException(409, f"任务 {runner.running} 正在运行")
        s.supervisor.run_once(f"job:{job}", runner.run(job, f"web:{sess.user}", fns[job]))
        await s.audit(sess.user, f"job.{job}")
        return {"accepted": True}

    @app.post("/api/watchdog/reset")
    async def watchdog_reset(sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        _ops(s).reset_watchdog()
        await s.audit(sess.user, "watchdog.reset")
        return {"ok": True}

    @app.get("/api/backups")
    async def backups(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        from mcops.backup import backups_json

        o = _ops(s)
        return {
            "items": await asyncio.to_thread(backups_json, o.backup_cfg.backup_dir),
            "download_rate_kib": s.download_rate_kib,
        }

    @app.get("/api/backups/{name}/download", response_model=None)
    async def backup_download(
        name: str, request: Request, sess: SessionDep, s: SvcDep
    ) -> StreamingResponse:
        from mcops import download
        from mcops.backup import list_backups

        o = _ops(s)
        path = next(
            (p for p in await asyncio.to_thread(list_backups, o.backup_cfg.backup_dir)
             if p.name == name),
            None,
        )  # fmt: skip
        if path is None:
            raise HTTPException(404, "备份不存在")
        rng = request.headers.get("range")
        download.acquire_slot()
        try:
            resp = download.file_response(path, s.download_rate_kib, rng)
        except BaseException:
            download.release_slot()
            raise
        if not rng or rng.startswith("bytes=0-"):  # 断点续传的后续请求不重复记审计
            await s.audit(sess.user, "backup.download", name, rate_kib=s.download_rate_kib)
        return resp

    @app.get("/api/restic/{snapshot}/download", response_model=None)
    async def restic_download(snapshot: str, sess: SessionDep, s: SvcDep) -> StreamingResponse:
        from mcops import download
        from mcops.restic import ResticError

        o = _ops(s)
        if o.restic is None:
            raise HTTPException(409, "未启用增量快照")
        if not re.fullmatch(r"[0-9a-f]{8,64}", snapshot):
            raise HTTPException(400, "快照 ID 不合法")
        download.acquire_slot()
        try:
            proc = await o.restic.dump_zip(snapshot)
        except (ResticError, OSError) as e:
            download.release_slot()
            raise HTTPException(500, f"导出失败：{e}") from None
        await s.audit(sess.user, "restic.download", snapshot, rate_kib=s.download_rate_kib)
        return download.process_response(
            proc, f"mc-snapshot-{snapshot[:8]}.zip", "application/zip", s.download_rate_kib
        )

    async def _repos(o: Any) -> list[dict[str, str]]:
        out = [{"id": "local", "name": "本机"}]
        if o.offsite_targets is not None:
            with contextlib.suppress(Exception):  # 凭据不全时只列本机
                out += [{"id": t.id, "name": t.name} for t in await o.offsite_targets()]
        return out

    @app.get("/api/restic")
    async def restic_info(
        _: SessionDep,
        s: SvcDep,
        repo: Annotated[str, Query(pattern=r"^(local|nas|s3)$")] = "local",
    ) -> dict[str, Any]:
        o = _ops(s)
        if o.restic is None:
            return {"enabled": False, "snapshots": [], "stats": None}
        repos = await _repos(o)
        try:
            target = await o.find_repo(repo)
        except KeyError:
            raise HTTPException(404, "没有这个仓库") from None
        base = {
            "enabled": True,
            "repo": repo,
            "repos": repos,
            "restore_running": o.jobs.running,
            "warn_minutes": o.cfg.warn_minutes,
        }
        try:
            snaps = await o.restic.snapshots(target)
            # 异地仓库的 stats 要把整个仓库索引读一遍，经 SMB 较慢，只在本机仓库上算
            stats = await o.restic.stats() if snaps and target is None else None
        except Exception as e:  # 仓库尚未初始化、NAS 不在线等
            return {**base, "error": str(e), "snapshots": [], "stats": None}
        return {**base, "snapshots": snaps, "stats": stats}

    @app.post("/api/restic/{snapshot}/restore", status_code=202)
    async def restic_restore(
        snapshot: str, body: RestoreBody, sess: SessionDep, s: SvcDep
    ) -> dict[str, bool]:
        o = _ops(s)
        if o.restic is None:
            raise HTTPException(409, "未启用增量快照")
        if not re.fullmatch(r"[0-9a-f]{8,64}", snapshot):
            raise HTTPException(400, "快照 ID 不合法")
        try:
            await o.find_repo(body.repo)
        except KeyError:
            raise HTTPException(404, "没有这个仓库") from None
        if o.jobs.running:
            raise HTTPException(409, f"任务 {o.jobs.running} 正在运行")
        fn = o.snapshot_restore(snapshot, body.scope, body.repo)
        s.supervisor.run_once(
            "job:snapshot_restore", o.jobs.run("snapshot_restore", f"web:{sess.user}", fn)
        )
        await s.audit(sess.user, "restic.restore", snapshot, repo=body.repo, scope=body.scope)
        return {"accepted": True}

    # ---- 设置 → 通知（M4） ----

    async def _notify_view(s: Services) -> dict[str, Any]:
        from mcops.bot.texts import mask_openid

        hub = s.notify
        ns = hub.settings
        alert_ids = {g.openid for g in ns.qq.groups}
        known: list[dict[str, Any]] = []
        bot_state = {"enabled": False, "connected": False}
        if s.bot is not None:
            st = await s.bot.status()
            bot_state = {"enabled": bool(st["enabled"]), "connected": bool(st["connected"])}
            for g in await s.bot.core.groups():
                added = g.get("added_at")
                known.append(
                    {
                        "openid": g["openid"],
                        "openid_masked": mask_openid(g["openid"]),
                        "added_at": added.isoformat() if added else None,
                        "is_alert": g["openid"] in alert_ids,
                    }
                )
        return {
            "email": {**ns.email.model_dump(), "has_password": hub.has_password},
            "qq": ns.qq.model_dump(),
            "rules": [
                {
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "default_severity": r.severity,
                    "enabled": ns.rules[r.id].enabled if r.id in ns.rules else True,
                    "severity": ns.rules[r.id].severity if r.id in ns.rules else None,
                    "unavailable": s.unavailable_rules.get(r.id),
                }
                for r in sec.RULES.values()
            ],
            "known_groups": known,
            "bot": bot_state,
            "stats": hub.stats(),
        }

    @app.get("/api/settings/notify")
    async def notify_get(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return await _notify_view(s)

    @app.put("/api/settings/notify")
    async def notify_put(body: dict[str, Any], sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        from pydantic import ValidationError

        from mcops.notifysettings import NotifySettings

        hub = s.notify
        body = dict(body)
        email = dict(body.get("email") or {})
        password = email.pop("password", None)
        email.pop("has_password", None)
        rules = {k: v for k, v in hub.settings.model_dump()["rules"].items()}
        unknown = set(body.get("rules") or {}) - set(sec.RULES)
        if unknown:
            raise HTTPException(422, f"未知规则：{', '.join(sorted(unknown))}")
        rules.update(body.get("rules") or {})
        # 与默认值相同的覆盖不存，保持设置干净
        rules = {
            k: v for k, v in rules.items() if not (v.get("enabled", True) and not v.get("severity"))
        }
        try:
            new = NotifySettings.model_validate(
                {"email": email, "qq": body.get("qq") or {}, "rules": rules}
            )
            if password is not None and not isinstance(password, str):
                raise ValueError("password 必须是字符串")
            await hub.save(new, password)
        except ValidationError as e:
            first = e.errors()[0]
            loc = ".".join(str(x) for x in first["loc"])
            raise HTTPException(
                422, f"{loc}：{first['msg'].removeprefix('Value error, ')}"
            ) from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        changed = "password" if password else "settings"
        await s.audit(sess.user, f"settings.notify.{changed}")
        return await _notify_view(s)

    @app.post("/api/settings/notify/test")
    async def notify_test(body: dict[str, Any], sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        ch = str(body.get("channel", ""))
        if ch not in ("email", "qq"):
            raise HTTPException(422, "channel 必须是 email 或 qq")
        ok, detail = await s.notify.test(ch)
        await s.audit(sess.user, f"settings.notify.test.{ch}")
        return {"ok": ok, "detail": detail}

    # ---- QQ 机器人 / 白名单 ----

    def _bot(s: Services) -> Any:
        if s.bot is None:
            raise HTTPException(503, "bot_not_configured")
        return s.bot

    @app.get("/api/bot/status")
    async def bot_status(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        st: dict[str, Any] = await _bot(s).status()
        return st

    @app.get("/api/bot/applications")
    async def bot_apps(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        items = await _bot(s).core.applications()
        return {
            "items": [
                {"name": a["name"], "openid_masked": mask_openid(a["openid"]), "ts": _iso(a["ts"])}
                for a in items
            ]
        }

    @app.get("/api/bot/grants")
    async def bot_grants(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        core = _bot(s).core
        items = await core.grants(limit=100)
        return {
            "activation_hours": core.s.activation_hours,
            "items": [
                {
                    "name": g["name"],
                    "openid_masked": mask_openid(g["openid"]),
                    "granted_at": _iso(g["granted_at"]),
                    "deadline": _iso(g["deadline"]),
                    "status": g["status"],
                    "resolved_at": _iso(g["resolved_at"]) if g["resolved_at"] else None,
                }
                for g in items
            ],
        }

    def _check_name(name: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", name):
            raise HTTPException(400, "游戏ID 不合法：只允许字母 / 数字 / 下划线，长度 3-16")

    @app.post("/api/bot/applications/{name}/approve")
    async def bot_approve(name: str, sess: SessionDep, s: SvcDep) -> dict[str, str]:
        _check_name(name)
        reply = await _bot(s).core.approve(name, f"web:{sess.user}")
        await s.audit(sess.user, "whitelist.approve", target=name, reply=reply[:200])
        return {"reply": reply}

    @app.post("/api/bot/applications/{name}/reject")
    async def bot_reject(name: str, sess: SessionDep, s: SvcDep) -> dict[str, str]:
        _check_name(name)
        reply = await _bot(s).core.reject(name, f"web:{sess.user}")
        await s.audit(sess.user, "whitelist.reject", target=name)
        return {"reply": reply}

    @app.get("/api/bot/interactions")
    async def bot_interactions(
        _: SessionDep,
        s: SvcDep,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        q: Annotated[str | None, Query(max_length=100)] = None,
    ) -> dict[str, Any]:
        rows = await _bot(s).core.interactions(limit, q)
        return {
            "items": [
                {
                    "id": r["id"],
                    "ts": _iso(r["ts"]),
                    "group_masked": mask_openid(r["group_openid"]),
                    "openid_masked": mask_openid(r["openid"]),
                    "admin": r["admin"],
                    "cmd": r["cmd"],
                    "input": r["input"],
                    "output": r["output"],
                }
                for r in rows
            ]
        }

    @app.post("/api/bot/simulate")
    async def bot_simulate(body: SimulateBody, sess: SessionDep, s: SvcDep) -> dict[str, str]:
        """指令模拟器：不发到 QQ。以管理员身份模拟时会真实执行白名单写操作，所以记审计。"""
        rt = _bot(s)
        actor = (
            next(iter(rt.core.s.admin_openids), "WEB_ADMIN")
            if body.as_admin
            else "WEB_SIMULATOR_GUEST"
        )
        group = next(iter(rt.core.s.allowed_groups), "WEB_SIMULATOR")
        from mcops.bot.core import Message

        cmd, reply = await rt.core.handle_message(
            Message(body.content, group, actor), simulated=True
        )
        await s.audit(
            sess.user, "bot.simulate", target=body.content[:100], cmd=cmd, as_admin=body.as_admin
        )
        return {"cmd": cmd, "reply": reply}

    @app.get("/api/whitelist")
    async def whitelist(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        rt = _bot(s)
        reg = s.files.easyauth_registered() if s.files else None
        entries = rt.core.wl.load() if rt.core.wl else []
        members = await rt.members_by_name()
        return {
            "items": [
                {
                    "name": e.get("name", ""),
                    "uuid": e.get("uuid", ""),
                    "easyauth_registered": None
                    if reg is None
                    else e.get("name", "").lower() in reg,
                    "members": [mask_openid(o) for o in members.get(e.get("name", "").lower(), [])],
                }
                for e in entries
            ]
        }

    @app.post("/api/whitelist")
    async def whitelist_add(body: WhitelistAddBody, sess: SessionDep, s: SvcDep) -> dict[str, str]:
        _check_name(body.name)
        reply = await _bot(s).core.do_add(body.name, f"web:{sess.user}", "web-add")
        await s.audit(sess.user, "whitelist.add", target=body.name, reply=reply[:200])
        return {"reply": reply}

    @app.delete("/api/whitelist/{name}")
    async def whitelist_remove(name: str, sess: SessionDep, s: SvcDep) -> dict[str, str]:
        _check_name(name)
        reply = await _bot(s).core.do_remove(name, f"web:{sess.user}")
        await s.audit(sess.user, "whitelist.remove", target=name, reply=reply[:200])
        return {"reply": reply}

    # ---- WebSocket 控制台 ----

    @app.websocket("/api/ws/console")
    async def ws_console(ws: WebSocket) -> None:
        s: Services = ws.app.state.svc
        host = ws.headers.get("host")
        if not _host_ok(host, s.api_cfg) or not _origin_ok(
            ws.headers.get("origin"), host, s.api_cfg
        ):
            await ws.close(code=4403)
            return
        sess = await s.auth.get_session(ws.cookies.get(SESSION_COOKIE))
        if sess is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        await ws.send_json({"kind": "status", "status": s.server.status_snapshot()})

        async with (
            s.server.log_lines.subscribe() as logs,
            s.server.status_updates.subscribe() as statuses,
            s.bus.events.subscribe() as evs,
            s.alerts.subscribe() as alerts_q,
        ):

            async def pump() -> None:
                while True:
                    getters: dict[asyncio.Future[Any], str] = {
                        asyncio.ensure_future(logs.get()): "log",
                        asyncio.ensure_future(statuses.get()): "status",
                        asyncio.ensure_future(evs.get()): "event",
                        asyncio.ensure_future(alerts_q.get()): "alert",
                    }
                    try:
                        done, _ = await asyncio.wait(getters, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        # pump 被取消（浏览器刷新 / 断开）时也要撤掉挂起的 get，
                        # 否则会留下 "Task was destroyed but it is pending!"
                        for g in getters:
                            if not g.done():
                                g.cancel()
                    for d in done:
                        item = d.result()
                        if isinstance(item, Event):
                            if item.type == "frps_conn":
                                continue  # 高频，不推给前端
                            await ws.send_json({"kind": "event", "event": item.to_json()})
                        elif getters[d] == "alert":
                            await ws.send_json({"kind": "alert", "alert": item})
                        else:
                            await ws.send_json(item)

            async def receive() -> None:
                while True:
                    msg = await ws.receive_json()
                    if not isinstance(msg, dict) or msg.get("kind") != "command":
                        continue
                    cmd = str(msg.get("command", ""))[:1000]
                    if not cmd:
                        continue
                    try:
                        out = await s.server.command(cmd)
                    except Exception as e:  # 包括 ConflictError / RCON 错误
                        out = f"[错误] {e}"
                    await s.audit(sess.user, "server.command", target=cmd, via="ws")
                    await ws.send_json({"kind": "command_result", "command": cmd, "output": out})

            tasks = [asyncio.create_task(pump()), asyncio.create_task(receive())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            except WebSocketDisconnect:
                pass
            finally:
                for t in tasks:
                    t.cancel()
                for t in tasks:
                    with contextlib.suppress(
                        asyncio.CancelledError, WebSocketDisconnect, Exception
                    ):
                        await t

    return app


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return (ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts).isoformat()


class _ImmutableStatic(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


def mount_static(app: FastAPI, static_dir: Path) -> None:
    """提供前端构建产物；未知路径回退到 index.html（SPA history 路由）。"""
    index = static_dir / "index.html"
    # 构建产物文件名带内容哈希：可永久缓存，并压缩后再走 SSH/frp 隧道
    app.mount(
        "/assets",
        GZipMiddleware(_ImmutableStatic(directory=static_dir / "assets"), minimum_size=1024),
        name="assets",
    )

    # M4 页面（各自独立模块，必须在 SPA 兜底路由之前注册）
    from mcops import api_config, api_game, api_logs, api_mods, api_network, api_system

    api_mods.register(app)
    api_logs.register(app)
    api_network.register(app)
    api_config.register(app)
    api_game.register(app)
    api_system.register(app)

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        # index.html 每次都重新验证，部署后才能拿到新的分块文件名
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
