"""API：logs 页面（M4）。只读；observe 模式下也可用。

在 create_app 里、SPA 兜底路由之前调用 register(app)。路由全部挂在 /api/logs/* 下。
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from mcops import logs

if TYPE_CHECKING:
    from mcops.api import SessionDep, SvcDep


def register(app: FastAPI) -> None:
    """在 create_app 里、SPA 兜底路由之前调用。"""
    from mcops.api import SessionDep, SvcDep

    # 本模块用 `from __future__ import annotations`，注解是字符串，FastAPI 靠模块
    # 全局名字空间解析它们；把依赖别名放进全局，路由签名里的 SessionDep/SvcDep 才生效。
    globals()["SessionDep"] = SessionDep
    globals()["SvcDep"] = SvcDep

    def _game_dir(s: Any) -> Any:
        return s.server.cfg.game_dir

    def _levels(level: str | None) -> set[str] | None:
        if not level:
            return None
        wanted = {x.strip().upper() for x in level.split(",") if x.strip()}
        bad = wanted - set(logs.LEVELS)
        if bad:
            raise HTTPException(422, f"未知的日志级别：{', '.join(sorted(bad))}")
        return wanted or None

    def _handle(e: logs.LogsError) -> HTTPException:
        return HTTPException(e.status, e.detail)

    @app.get("/api/logs/sources")
    async def logs_sources(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return await logs.list_sources(_game_dir(s))

    @app.get("/api/logs/view")
    async def logs_view(
        _: SessionDep,
        s: SvcDep,
        source: Annotated[str, Query(min_length=1, max_length=128)],
        tail: Annotated[int | None, Query(ge=1, le=logs.TAIL_MAX)] = None,
        offset: Annotated[int | None, Query(ge=0)] = None,
        limit: Annotated[int | None, Query(ge=1, le=logs.TAIL_MAX)] = None,
        level: Annotated[str | None, Query(max_length=64)] = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
        regex: bool = False,
    ) -> dict[str, Any]:
        levels = _levels(level)
        rx: re.Pattern[str] | None = None
        if regex and q:
            try:
                rx = logs.compile_regex(q)
            except logs.LogsError as e:
                raise _handle(e) from None
        game_dir = _game_dir(s)
        try:
            if source.startswith("journal:"):
                key = source.split(":", 1)[1]
                lines = tail or logs.JOURNAL_DEFAULT_LINES
                return await logs.read_journal(key, s.server.tz, lines=lines)
            if source.startswith("crash:"):
                path = logs.resolve_crash(game_dir, source.split(":", 1)[1])
                fd = datetime.fromtimestamp(path.stat().st_mtime).date()
            else:
                path = logs.resolve_game_log(game_dir, source)
                fd = logs.file_date_for(game_dir, source)
            return await asyncio.to_thread(
                logs.read_view,
                path,
                fd,
                s.server.tz,
                tail=tail,
                offset=offset,
                limit=limit,
                levels=levels,
                q=None if (regex and rx is not None) else q,
                rx=rx,
            )
        except logs.LogsError as e:
            raise _handle(e) from None

    @app.get("/api/logs/search")
    async def logs_search(
        _: SessionDep,
        s: SvcDep,
        q: Annotated[str, Query(min_length=1, max_length=200)],
        from_: Annotated[str | None, Query(alias="from", max_length=10)] = None,
        to: Annotated[str | None, Query(max_length=10)] = None,
        level: Annotated[str | None, Query(max_length=64)] = None,
        regex: bool = False,
    ) -> dict[str, Any]:
        levels = _levels(level)
        today = datetime.now(s.server.tz).date()
        try:
            date_to = _parse_date(to) if to else today
            date_from = (
                _parse_date(from_)
                if from_
                else date_to - timedelta(days=logs.SEARCH_DEFAULT_DAYS - 1)
            )
        except ValueError:
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from None
        if date_from > date_to:
            raise HTTPException(422, "起始日期不能晚于结束日期")
        if (date_to - date_from).days + 1 > logs.SEARCH_MAX_DAYS:
            raise HTTPException(422, f"日期范围最多 {logs.SEARCH_MAX_DAYS} 天")
        rx: re.Pattern[str] | None = None
        if regex:
            try:
                rx = logs.compile_regex(q)
            except logs.LogsError as e:
                raise _handle(e) from None
        return await asyncio.to_thread(
            logs.search_files,
            _game_dir(s),
            s.server.tz,
            q=q,
            date_from=date_from,
            date_to=date_to,
            levels=levels,
            rx=rx,
        )

    @app.get("/api/logs/download")
    async def logs_download(
        _: SessionDep,
        s: SvcDep,
        source: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> FileResponse:
        if source.startswith("journal:"):
            raise HTTPException(400, "系统日志不提供下载")
        game_dir = _game_dir(s)
        try:
            if source.startswith("crash:"):
                path = logs.resolve_crash(game_dir, source.split(":", 1)[1])
            else:
                path = logs.resolve_game_log(game_dir, source)
        except logs.LogsError as e:
            raise _handle(e) from None
        return FileResponse(
            path,
            media_type=logs.download_media_type(path),
            filename=path.name,
        )


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)
