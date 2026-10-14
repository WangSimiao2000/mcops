"""agent 入口：组装各服务，挂上后台任务，启动 uvicorn（只监听回环地址）。"""

from __future__ import annotations

import argparse
import logging
import logging as _logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import capabilities
from mcops.api import Services, create_app, mount_static
from mcops.auth import Authenticator
from mcops.backend import make_backend
from mcops.backup import BackupConfig
from mcops.bot.channel import LegacyMcShChannel, RconChannel, ServerChannel
from mcops.bot.core import BotCore, BotSettings
from mcops.bot.migrate import import_legacy
from mcops.bot.qq import QQClient, run_qq
from mcops.bot.runtime import BotRuntime
from mcops.bot.whitelist import WhitelistFile
from mcops.config import Config, LoadedConfig, Secrets, load_config
from mcops.configstore import ConfigStore, read_overlay_sync
from mcops.db import open_db
from mcops.edge import EdgeCollector
from mcops.events import Event, EventBus
from mcops.flight import FlightWatch, is_fall_flying
from mcops.hoststats import HostStats
from mcops.notify import NotifyHub
from mcops.notifysettings import AlertGroup, EmailSettings, NotifySettings, QQSettings, RuleOverride
from mcops.ops import Ops, OpsConfig, install_game_hooks
from mcops.pipeline import Pipeline, run_live, run_ticks, run_whitelist_audit
from mcops.players import PlayerTracker, ServerFiles, online_roster
from mcops.rcon import RconClient
from mcops.restic import (
    OffsiteTarget,
    Restic,
    ResticError,
    RetentionPolicy,
    rclone_obscure,
    s3_target,
    smb_target,
)
from mcops.retention import retention_loop
from mcops.security import SecurityConfig, SecurityEngine
from mcops.server import ServerController, State
from mcops.supervisor import Supervisor

log = _logging.getLogger(__name__)


def build_app(loaded: LoadedConfig, config_path: Path | None = None) -> FastAPI:
    cfg, sec = loaded.config, loaded.secrets
    observe = cfg.mode == "observe"

    async def factory() -> Services:
        engine = await open_db(cfg.db_path)
        bus = EventBus(engine)
        rcon = RconClient(
            cfg.server.rcon_host,
            cfg.server.rcon_port,
            sec.rcon_password.get_secret_value() if sec.rcon_password else "",
        )
        tz = cfg.tzinfo()
        server = ServerController(
            cfg.server,
            make_backend(cfg.server, cfg.data_dir, observe=observe),
            rcon,
            bus,
            tz,
            observe=observe,
        )
        server.roster = lambda: online_roster(engine)
        auth = Authenticator(
            engine,
            sec.admin_password_hash.get_secret_value(),
            sec.totp_secret.get_secret_value() if sec.totp_secret else None,
            cfg.api.session_ttl_s,
        )
        files = ServerFiles(cfg.server.game_dir, cfg.server.easyauth_db)

        edge = None
        if cfg.edge is not None:
            e = cfg.edge
            edge = EdgeCollector(
                engine,
                bus,
                e.host,
                e.user,
                e.key_file,
                e.known_hosts,
                e.port,
                e.retention_days,
                e.public_ports,
            )

        sup = Supervisor()
        svc = Services(cfg.api, engine, bus, server, auth, sup, files, edge, cfg.mode)
        svc.secrets_file = cfg.secrets_file
        svc.download_rate_kib = cfg.ops.download_rate_kib
        svc.caps = capabilities.detect(
            cfg.server.game_dir,
            cfg.server.jar,
            {
                "edge": cfg.edge is not None,
                "bot": cfg.bot is not None,
                "restic": cfg.ops.restic.enabled,
                "systemd": cfg.server.backend == "systemd",
                "flight_watch": cfg.ops.flight_watch,
                "control": not observe,
            },
        )
        rules = cfg.security

        async def elytra_probe(name: str) -> bool | None:
            if server.state is not State.RUNNING:
                return None
            return await is_fall_flying(server.rcon.command, name)

        security = SecurityEngine(
            engine,
            tz,
            SecurityConfig(
                night_start_hour=rules.night_start_hour,
                night_end_hour=rules.night_end_hour,
                ip_allowlist=set(rules.ip_allowlist),
                disabled_rules=set(rules.disabled_rules),
                elytra_burst_count=rules.elytra_burst_count,
                elytra_burst_window_minutes=rules.elytra_burst_window_minutes,
            ),
            on_alert=svc.alerts.publish,
            elytra_probe=elytra_probe,
            banned_ips=files.banned_ips,
        )
        svc.disabled_rules = security.cfg.disabled_rules  # 同一个 set，面板修改即时生效
        # 依赖的信号收不到的规则（如 EasyAuth 拦掉 carpet 握手时的指纹规则）强制停用
        svc.unavailable_rules = capabilities.unavailable_rules(svc.caps, cfg.server.game_dir)
        security.cfg.disabled_rules.update(svc.unavailable_rules)

        def apply_live(new: Config) -> None:
            r = new.security
            security.cfg.night_start_hour = r.night_start_hour
            security.cfg.night_end_hour = r.night_end_hour
            security.cfg.ip_allowlist = set(r.ip_allowlist)
            security.cfg.elytra_burst_count = r.elytra_burst_count
            security.cfg.elytra_burst_window_minutes = r.elytra_burst_window_minutes
            svc.download_rate_kib = new.ops.download_rate_kib

        if config_path is not None:
            svc.config = ConfigStore(engine, config_path, cfg, on_live=apply_live)

        def apply_rules(ns: NotifySettings) -> None:
            security.cfg.disabled_rules.clear()
            security.cfg.disabled_rules.update(r for r, o in ns.rules.items() if not o.enabled)
            security.cfg.disabled_rules.update(svc.unavailable_rules)
            security.cfg.severity_overrides.clear()
            security.cfg.severity_overrides.update(
                {r: o.severity for r, o in ns.rules.items() if o.severity}
            )
            if svc.bot is not None:
                svc.bot.core.s.alert_groups = {g.openid for g in ns.qq.groups}

        hub = NotifyHub(engine, on_change=apply_rules)
        if await hub.load(
            _seed_notify(cfg), sec.smtp_password.get_secret_value() if sec.smtp_password else None
        ):
            log.info("通知设置已从 mcops.yaml 导入，之后请在面板「设置 → 通知」修改")
        security.notifier = hub
        svc.notify = hub
        pipeline = Pipeline(PlayerTracker(engine), security)

        bot_rt = await _build_bot(cfg, sec, engine, server, files, observe, rcon, sup)
        svc.bot = bot_rt
        if bot_rt is not None:
            hub.qq_push = bot_rt.core.push
            client = bot_rt.client
            hub.qq_ready = lambda: bool(bot_rt.enabled and client is not None and client.connected)
            hub.qq_error = lambda: client.last_error if client is not None else None
            bot_rt.core.alert_group_hook = hub.set_alert_group
            bot_rt.core.ui_publish = bus.events.publish
            apply_rules(hub.settings)

        if not observe:
            o = cfg.ops
            assert o.backup_dir is not None
            ops = Ops(
                engine,
                bus,
                server,
                BackupConfig(
                    cfg.server.game_dir,
                    o.backup_dir,
                    o.backup_keep_days,
                    o.backup_min_keep,
                    tuple(o.backup_exclude),
                ),
                OpsConfig(
                    o.restart_time, o.warn_minutes, o.crash_threshold, o.crash_window_minutes
                ),
                tz,
                restic=_make_restic(cfg, sec),
                offsite_targets=_make_offsite(cfg, sec),
                announce_maintenance=bot_rt.core.begin_maintenance
                if bot_rt and bot_rt.enabled
                else None,
                raise_alert=security.raise_alert,
            )
            svc.ops = ops
            if bot_rt is not None:
                bot_rt.core.planned_source = lambda: server.planned_by
            install_game_hooks(cfg, server)
            if svc.config is not None:
                store = svc.config

                async def note_mc_start() -> None:
                    await store.mark_mc_started()

                server.pre_start_hooks.append(note_mc_start)
            sup.spawn("scheduler", ops.scheduler_loop)
            sup.spawn("watchdog", ops.watchdog_loop)
            if o.flight_watch:
                fw = FlightWatch(
                    server.rcon.command,
                    security.raise_alert,
                    tz,
                    lambda: server.state is State.RUNNING,
                    o.flight_interval_s,
                    o.flight_samples,
                    (cfg.security.night_start_hour, cfg.security.night_end_hour),
                )
                sup.spawn("flight_watch", fw.loop)
            if ops.restic is not None:
                ops.cfg.restic_interval_minutes = o.restic.interval_minutes
                sup.spawn("restic", ops.restic_loop)

        await server.adopt_existing()
        if svc.ops is not None:
            await svc.ops.recover_after_restart()
            if cfg.ops.autostart:
                await svc.ops.autostart()
        host_paths = {"游戏目录": cfg.server.game_dir, "数据库": cfg.data_dir}
        if cfg.ops.backup_dir is not None:
            host_paths["冷备份"] = cfg.ops.backup_dir
        if cfg.ops.restic.enabled and cfg.ops.restic.repo.startswith("/"):
            host_paths["增量快照"] = Path(cfg.ops.restic.repo)
        svc.host = HostStats(host_paths, server.backend.pid)
        sup.spawn("host_stats", svc.host.loop)
        sup.spawn(
            "retention",
            lambda: retention_loop(
                engine, cfg.retention, cfg.edge.retention_days if cfg.edge else None
            ),
        )
        sup.spawn("log_ingest", server.ingest_logs)
        sup.spawn("process_watch", server.watch_process)
        sup.spawn("status_poll", server.poll_status)
        sup.spawn("pipeline", lambda: run_live(bus, pipeline))
        sup.spawn("security_tick", lambda: run_ticks(security))
        sup.spawn("whitelist_audit", lambda: run_whitelist_audit(security, files))
        if edge is not None:
            sup.spawn("edge_frps", edge.run)
        await bus.emit(Event("agent_started", "agent", {"mode": cfg.mode}))
        return svc

    app = create_app(factory)
    if cfg.api.static_dir is not None and (cfg.api.static_dir / "index.html").is_file():
        mount_static(app, cfg.api.static_dir)
    return app


async def _build_bot(
    cfg: Config,
    sec: Secrets,
    engine: AsyncEngine,
    server: ServerController,
    files: ServerFiles,
    observe: bool,
    rcon: RconClient,
    sup: Supervisor,
) -> BotRuntime | None:
    b = cfg.bot
    if b is None:
        return None
    channel: ServerChannel | None
    if not observe:
        channel = RconChannel(rcon)
    elif b.legacy_mc_sh is not None:
        channel = LegacyMcShChannel(b.legacy_mc_sh, cfg.server.latest_log)
    else:
        channel = None

    async def roster() -> tuple[list[str], list[str]]:
        return await online_roster(engine)

    core = BotCore(
        engine,
        BotSettings(
            display_host=b.display_host,
            query_port=cfg.server.port,
            join_note=b.join_note,
            name=b.name,
            admin_openids=set(b.admin_openids),
            allowed_groups=set(b.allowed_groups),
            alert_groups=set(b.alert_groups),
            max_pending_per_user=b.max_pending_per_user,
            max_pending_total=b.max_pending_total,
            monitor_interval=b.monitor_interval,
            fail_threshold=b.fail_threshold,
            active_alert=b.active_alert,
            planned_actors=set(b.planned_actors),
            watchdog_state_file=b.watchdog_state_file,
            maintenance_signal_file=b.maintenance_signal_file,
            maintenance_cooldown=b.maintenance_cooldown,
            activation_hours=b.activation_hours,
            frp_server_port=b.frp_server_port,
        ),
        WhitelistFile(cfg.server.game_dir / "whitelist.json"),
        channel,
        cfg.server.game_dir,
        server.backend.pid,
        roster,
        # 没装 EasyAuth（库文件不存在）时，"注册"无从谈起，激活只看是否进过服
        files.easyauth_registered
        if cfg.server.easyauth_db is not None and cfg.server.easyauth_db.exists()
        else None,
    )
    if b.legacy_bot_dir is not None and b.legacy_bot_dir.is_dir():
        counts = await import_legacy(engine, b.legacy_bot_dir)
        log.info("导入旧机器人数据：%s", counts)

    client = None
    enabled = b.enabled and sec.qq_appid is not None and sec.qq_secret is not None
    if b.enabled and not enabled:
        log.error("bot.enabled=true 但 secrets.yaml 缺少 qq_appid / qq_secret，机器人不会连接 QQ")
    if enabled:
        assert sec.qq_appid is not None and sec.qq_secret is not None
        client = QQClient(core)
        appid, secret = sec.qq_appid.get_secret_value(), sec.qq_secret.get_secret_value()
        sup.spawn("qq_bot", lambda: run_qq(client, appid, secret))
        sup.spawn("bot_monitor", core.monitor_loop)
        # 旧脚本的维护信号文件；control 模式下维护播报由 ops 直接调用，不需要这个模块
        if b.maintenance_signal_file is not None:
            sup.spawn("bot_maintenance", core.maintenance_watcher)
    # 注册期限与是否连 QQ 无关：面板里批准的申请同样要按期注册
    sup.spawn("whitelist_activation", core.activation_loop)
    return BotRuntime(engine, core, client, enabled)


def run() -> None:
    ap = argparse.ArgumentParser(prog="mcops-agent")
    ap.add_argument("--config", required=True, help="mcops.yaml 路径")
    args = ap.parse_args()
    logging.basicConfig(
        level="INFO",
        format="%(levelname)s %(name)s: %(message)s",  # journald 自带时间戳
        force=True,
    )
    path = Path(args.config).resolve()
    loaded = load_with_overlay(path)
    logging.getLogger().setLevel(loaded.config.log_level)
    uvicorn.run(
        build_app(loaded, path),
        host=loaded.config.api.host,
        port=loaded.config.api.port,
        log_level=loaded.config.log_level.lower(),
        proxy_headers=False,
        log_config=None,  # 沿用上面的根日志配置，否则 uvicorn 会吞掉 mcops.* 的日志
    )


def load_with_overlay(path: Path) -> LoadedConfig:
    """mcops.yaml + 面板保存的覆盖层。覆盖层导致配置无效时退回纯 yaml 启动，保证 agent 永远起得来。"""
    base = load_config(path)
    overlay = read_overlay_sync(base.config.db_path)
    if not overlay:
        return base
    try:
        return load_config(path, overlay)
    except Exception as e:
        log.error("面板保存的配置无效，本次按 mcops.yaml 启动（请到面板「修改历史」恢复）：%s", e)
        return base


def _make_offsite(cfg: Config, sec: Secrets) -> Callable[[], Awaitable[list[OffsiteTarget]]] | None:
    """按配置组装异地仓库；一个都没配则返回 None（面板不显示异地任务）。配置不全时任务失败并告警。"""
    o = cfg.ops.restic.offsite
    if not cfg.ops.restic.enabled or not (o.s3_repo or o.smb_host):
        return None

    async def build() -> list[OffsiteTarget]:
        out: list[OffsiteTarget] = []
        ak, sk = sec.offsite_s3_access_key, sec.offsite_s3_secret_key
        if o.s3_repo:
            if ak is None or sk is None:
                raise ResticError("已填对象存储地址，但「机密」里还没填 SecretId / SecretKey")
            out.append(
                s3_target(
                    o.s3_repo, ak.get_secret_value(), sk.get_secret_value(), o.limit_upload_kib
                )
            )
        if o.smb_host:
            if not o.smb_share:
                raise ResticError("已填 NAS 地址，但没填共享名")
            user = sec.offsite_smb_user.get_secret_value() if sec.offsite_smb_user else ""
            pw = sec.offsite_smb_password.get_secret_value() if sec.offsite_smb_password else ""
            obscured = await rclone_obscure(o.rclone_binary, pw) if pw else ""
            out.append(
                smb_target(
                    o.smb_host,
                    o.smb_share,
                    o.smb_path,
                    user,
                    obscured,
                    o.rclone_binary,
                    o.limit_upload_kib,
                )
            )
        return out

    return build


def _make_restic(cfg: Config, sec: Secrets) -> Restic | None:
    r = cfg.ops.restic
    if not r.enabled:
        return None
    if sec.restic_password is None:
        log.error("ops.restic.enabled=true 但 secrets.yaml 缺少 restic_password，增量备份不会运行")
        return None
    return Restic(
        r.binary, r.repo, sec.restic_password.get_secret_value(), cfg.server.game_dir,
        tuple(cfg.ops.backup_exclude),
        RetentionPolicy(r.keep_hourly, r.keep_daily, r.keep_weekly, r.keep_monthly),
    )  # fmt: skip


def _seed_notify(cfg: Config) -> NotifySettings:
    """首次启动：把 mcops.yaml 里的旧通知配置搬进面板设置。"""
    email = EmailSettings()
    if cfg.smtp is not None:
        m = cfg.smtp
        email = EmailSettings(
            enabled=True, host=m.host, port=m.port, security="ssl" if m.port == 465 else "starttls",
            username=m.username or "", sender=m.sender, to=list(m.to),
            severities=[s for s in m.severities if s in ("critical", "warning", "info")],  # type: ignore[misc]
        )  # fmt: skip
    groups = [AlertGroup(openid=g) for g in (cfg.bot.alert_groups if cfg.bot else [])]
    rules = {r: RuleOverride(enabled=False) for r in cfg.security.disabled_rules}
    return NotifySettings(email=email, qq=QQSettings(groups=groups), rules=rules)
