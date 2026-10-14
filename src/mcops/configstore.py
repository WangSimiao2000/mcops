"""面板可编辑的配置（P1，docs/03-config-api.md）。

mcops.yaml 提供默认值；面板改过的字段存在 kv `config.overlay`（只含被改过的叶子），
生效配置 = yaml 深合并覆盖层。每次保存写 config_history，可回滚。

agent 只在启动时读配置，所以绝大多数改动「重启 agent 后生效」（MC 不掉线）；
少数字段由开服流程使用，「下次开服生效」；安全规则阈值支持立即生效。
"""

from __future__ import annotations

import copy
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import desc, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db
from mcops.config import Config, ConfigError, build_config, deep_merge, read_yaml_raw

log = logging.getLogger(__name__)

OVERLAY_KEY = "config.overlay"
MC_APPLIED_KEY = "config.mc_applied"
Apply = Literal["agent", "mc", "live"]

# ---- 可编辑范围 --------------------------------------------------------------

SECTIONS: list[dict[str, str]] = [
    {"key": "general", "title": "常规", "description": "时区与日志级别。"},
    {"key": "server", "title": "服务端", "description": "Java、内存、端口与进程托管。"},
    {"key": "ops", "title": "运维任务", "description": "定时重启、备份、增量快照、开服钩子与飞行检测。"},
    {"key": "security", "title": "安全规则", "description": "凌晨时段与 IP 白名单。规则开关与级别在「通知」里。"},
    {"key": "bot", "title": "QQ 机器人", "description": "管理员、授权群与对外文案。凭证在「账号与安全」里。"},
    {"key": "edge", "title": "云主机日志采集", "description": "通过受限只读 SSH 拉取 frps 日志。"},
    {"key": "retention", "title": "数据保留", "description": "本地数据库各类记录保留多少天，0 表示永久。"},
    {"key": "api", "title": "面板", "description": "会话有效期与额外允许的访问来源。监听地址只能在 mcops.yaml 里改。"},
]  # fmt: skip
GENERAL_FIELDS = ("timezone", "log_level")

# 不在面板展示的字段（启动必需 / 过渡期兼容 / 已迁到「通知」）
HIDDEN: set[str] = {
    "mode", "data_dir", "secrets_file", "smtp",
    "api.host", "api.port", "api.static_dir", "api.cookie_secure",
    "bot.legacy_mc_sh", "bot.watchdog_state_file", "bot.maintenance_signal_file",
    "bot.legacy_bot_dir", "bot.alert_groups", "security.disabled_rules",
    "server.easyauth_db", "server.rcon_host", "edge.public_ports",
}  # fmt: skip

ADVANCED: set[str] = {
    "server.backend", "server.game_dir", "server.jar", "server.java_bin", "server.systemd_unit",
    "server.systemd_user", "server.rcon_port", "server.start_ready_timeout_s",
    "server.stop_timeout_s", "server.manage_unit",
    "ops.crash_threshold", "ops.crash_window_minutes", "ops.backup_dir", "ops.backup_exclude",
    "ops.restic.binary", "ops.restic.repo", "ops.restic.offsite.rclone_binary",
    "ops.restic.offsite.smb_path", "ops.flight_interval_s", "ops.flight_samples",
    "bot.max_pending_per_user", "bot.max_pending_total", "bot.monitor_interval",
    "bot.fail_threshold", "bot.planned_actors", "bot.maintenance_cooldown", "bot.frp_server_port",
    "edge.user", "edge.port", "edge.key_file", "edge.known_hosts",
    "api.allowed_origins", "log_level",
}  # fmt: skip

APPLY_MC: set[str] = {
    "server.java_bin", "server.java_opts", "server.jar",
    "ops.spawn", "ops.motd", "ops.motd.server_list", "ops.motd.line1", "ops.motd.line2",
}  # fmt: skip
APPLY_LIVE: set[str] = {
    "security.night_start_hour", "security.night_end_hour", "security.ip_allowlist",
    "security.elytra_burst_count", "security.elytra_burst_window_minutes",
    "ops.download_rate_kib",
}  # fmt: skip

TITLES: dict[str, str] = {
    "timezone": "时区", "log_level": "日志级别",
    "server.backend": "托管方式", "server.game_dir": "游戏目录", "server.java_bin": "Java 路径",
    "server.java_opts": "JVM 参数", "server.jar": "服务端 jar", "server.systemd_unit": "systemd 单元名",
    "server.systemd_user": "使用用户级 systemd", "server.manage_unit": "由 mc-ops 生成 systemd 单元",
    "server.port": "游戏端口", "server.rcon_port": "RCON 端口",
    "server.start_ready_timeout_s": "开服就绪超时", "server.stop_timeout_s": "关服超时",
    "ops.autostart": "开机自动开服", "ops.restart_time": "每日定时重启", "ops.warn_minutes": "重启预警",
    "ops.crash_threshold": "崩溃次数阈值", "ops.crash_window_minutes": "崩溃统计窗口",
    "ops.backup_dir": "冷备份目录", "ops.backup_keep_days": "冷备份保留天数",
    "ops.backup_min_keep": "冷备份最少保留", "ops.backup_exclude": "冷备份排除目录",
    "ops.spawn": "开服后设置出生点", "ops.motd": "MOTD 同步", "ops.motd.server_list": "服务器列表 MOTD",
    "ops.motd.line1": "MiniMOTD 第一行", "ops.motd.line2": "MiniMOTD 第二行",
    "ops.restic": "增量快照（restic）", "ops.restic.enabled": "启用增量快照",
    "ops.restic.binary": "restic 路径", "ops.restic.repo": "仓库位置",
    "ops.restic.interval_minutes": "快照间隔", "ops.restic.keep_hourly": "保留最近快照数",
    "ops.restic.keep_daily": "保留每日快照（天）", "ops.restic.keep_weekly": "保留每周快照（周）",
    "ops.restic.keep_monthly": "保留每月快照（月）",
    "ops.restic.offsite": "异地副本", "ops.restic.offsite.s3_repo": "对象存储地址（S3/COS）",
    "ops.restic.offsite.smb_host": "NAS 地址（SMB）", "ops.restic.offsite.smb_share": "NAS 共享名",
    "ops.restic.offsite.smb_path": "NAS 内子目录", "ops.restic.offsite.rclone_binary": "rclone 路径",
    "ops.restic.offsite.limit_upload_kib": "异地上传限速",
    "ops.flight_watch": "悬空/飞行检测", "ops.flight_interval_s": "检测间隔", "ops.flight_samples": "判定采样次数",
    "ops.download_rate_kib": "备份下载限速",
    "security.night_start_hour": "凌晨时段开始", "security.night_end_hour": "凌晨时段结束",
    "security.ip_allowlist": "自己的 IP（白名单）",
    "security.elytra_burst_count": "鞘翅移动异常告警次数",
    "security.elytra_burst_window_minutes": "鞘翅移动异常统计窗口",
    "bot.enabled": "连接 QQ", "bot.name": "机器人名字", "bot.display_host": "对外显示的服务器地址", "bot.join_note": "进服说明",
    "bot.admin_openids": "管理员 openid", "bot.allowed_groups": "授权群 openid",
    "bot.max_pending_per_user": "每人最多待审申请", "bot.max_pending_total": "待审申请总上限",
    "bot.monitor_interval": "状态探测间隔", "bot.fail_threshold": "连续失败判定宕机",
    "bot.active_alert": "宕机/恢复时主动播报", "bot.planned_actors": "计划内停机来源",
    "bot.maintenance_cooldown": "维护播报冷却", "bot.activation_hours": "获批后注册期限",
    "bot.frp_server_port": "frps 端口（隧道延迟）",
    "edge.host": "云主机地址", "edge.user": "SSH 用户", "edge.port": "SSH 端口",
    "edge.key_file": "私钥文件", "edge.known_hosts": "known_hosts 文件", "edge.retention_days": "连接记录保留天数",
    "retention.events_days": "事件", "retention.bot_interactions_days": "QQ 交互记录",
    "retention.job_runs_days": "任务运行记录",
    "api.allowed_origins": "额外允许的访问来源", "api.session_ttl_s": "登录有效期",
}  # fmt: skip

UNITS: dict[str, str] = {
    "server.start_ready_timeout_s": "秒", "server.stop_timeout_s": "秒", "ops.warn_minutes": "分钟",
    "ops.crash_window_minutes": "分钟", "ops.backup_keep_days": "天", "ops.restic.interval_minutes": "分钟",
    "ops.flight_interval_s": "秒", "security.night_start_hour": "点", "security.night_end_hour": "点",
    "security.elytra_burst_count": "次", "security.elytra_burst_window_minutes": "分钟",
    "ops.download_rate_kib": "KiB/s", "ops.restic.offsite.limit_upload_kib": "KiB/s",
    "bot.monitor_interval": "秒", "bot.maintenance_cooldown": "秒", "bot.activation_hours": "小时", "edge.retention_days": "天",
    "retention.events_days": "天", "retention.bot_interactions_days": "天",
    "retention.job_runs_days": "天", "api.session_ttl_s": "秒",
}  # fmt: skip

WIDGETS: dict[str, str] = {
    "server.java_opts": "memory", "ops.restart_time": "time",
    "ops.backup_exclude": "lines", "security.ip_allowlist": "lines", "bot.admin_openids": "lines",
    "bot.allowed_groups": "lines", "bot.planned_actors": "lines", "api.allowed_origins": "lines",
}  # fmt: skip


def apply_of(path: str) -> Apply:
    if path in APPLY_LIVE:
        return "live"
    if path in APPLY_MC or any(path.startswith(p + ".") for p in APPLY_MC):
        return "mc"
    return "agent"


# ---- 路径工具 ----------------------------------------------------------------


def to_section_values(raw: dict[str, Any]) -> dict[str, Any]:
    """Config 的 JSON dump -> 面板结构（general 段收纳顶层字段，去掉隐藏字段）。"""
    out: dict[str, Any] = {"general": {k: raw.get(k) for k in GENERAL_FIELDS}}
    for s in SECTIONS:
        k = s["key"]
        if k != "general":
            out[k] = _strip_hidden(copy.deepcopy(raw.get(k)), k)
    return out


def from_section_values(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in values.items():
        if k == "general" and isinstance(v, dict):
            out.update({f: v[f] for f in GENERAL_FIELDS if f in v})
        else:
            out[k] = v
    return out


def _strip_hidden(v: Any, prefix: str) -> Any:
    if isinstance(v, dict):
        return {
            k: _strip_hidden(x, f"{prefix}.{k}")
            for k, x in v.items()
            if f"{prefix}.{k}" not in HIDDEN
        }
    return v


def flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """嵌套 dict -> {"a.b.c": 叶子}。list / None / 标量都是叶子。"""
    out: dict[str, Any] = {}
    for k, v in d.items():
        p = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and v:
            out.update(flatten(v, p))
        else:
            out[p] = v
    return out


def unflatten(flat: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path, v in flat.items():
        cur = out
        *parents, leaf = path.split(".")
        for p in parents:
            cur = cur.setdefault(p, {})
        cur[leaf] = v
    return out


def _visible(path: str) -> bool:
    parts = path.split(".")
    return not any(".".join(parts[: i + 1]) in HIDDEN for i in range(len(parts)))


# ---- schema ------------------------------------------------------------------


def editable_schema() -> dict[str, Any]:
    """Config 的 JSON Schema，去掉隐藏字段，按 SECTIONS 重组，并给字段加中文标题与 x-* 元数据。"""
    full = Config.model_json_schema()
    defs: dict[str, Any] = full.get("$defs", {})
    props: dict[str, Any] = full["properties"]

    def deref(node: dict[str, Any]) -> dict[str, Any] | None:
        ref = node.get("$ref")
        if ref is None:
            for alt in node.get("anyOf", []):
                if "$ref" in alt:
                    ref = alt["$ref"]
        return defs[ref.split("/")[-1]] if ref else None

    def decorate(node: dict[str, Any], path: str) -> dict[str, Any]:
        node = copy.deepcopy(node)
        target = deref(node)
        if target is not None:
            obj = copy.deepcopy(target)
            obj["properties"] = {
                k: decorate(v, f"{path}.{k}")
                for k, v in obj.get("properties", {}).items()
                if _visible(f"{path}.{k}")
            }
            obj.pop("required", None)
            if "anyOf" in node:  # Optional[Model]
                obj["nullable"] = True
            obj.pop("title", None)
            node = {**{k: v for k, v in node.items() if k not in ("$ref", "anyOf")}, **obj}
        node["title"] = TITLES.get(path, node.get("title", path.rsplit(".", 1)[-1]))
        node["x-apply"] = apply_of(path)
        if path in ADVANCED:
            node["x-advanced"] = True
        if path in UNITS:
            node["x-unit"] = UNITS[path]
        if path in WIDGETS:
            node["x-widget"] = WIDGETS[path]
        return node

    sections: dict[str, Any] = {
        "general": {
            "type": "object",
            "properties": {f: decorate(props[f], f) for f in GENERAL_FIELDS},
        }
    }
    for s in SECTIONS:
        k = s["key"]
        if k != "general":
            sections[k] = decorate(props[k], k)
    return {"type": "object", "properties": sections}


# ---- 存储 --------------------------------------------------------------------


def read_overlay_sync(db_path: Path) -> dict[str, Any]:
    """启动时（事件循环之前）读覆盖层。库不存在 / 表不存在 => 空。"""
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (OVERLAY_KEY,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        log.warning("读取面板配置失败，按 mcops.yaml 启动：%s", e)
        return {}
    if not row:
        return {}
    import json

    v = json.loads(row[0])
    return v if isinstance(v, dict) else {}


class ConfigValidationError(ValueError):
    pass


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    loc = ".".join(str(x) for x in err["loc"])
    return f"{loc}：{err['msg'].removeprefix('Value error, ')}"


class ConfigStore:
    def __init__(
        self,
        engine: AsyncEngine,
        yaml_path: Path,
        running: Config,
        on_live: Callable[[Config], None] | None = None,
    ) -> None:
        self.engine = engine
        self.yaml_path = yaml_path
        self.running = running  # agent 启动时采用的配置（判断哪些改动还没生效）
        self.on_live = on_live

    # ---- 读 ----

    async def overlay(self) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            v = (
                await conn.execute(select(db.kv.c.value).where(db.kv.c.key == OVERLAY_KEY))
            ).scalar()
        return v if isinstance(v, dict) else {}

    def _build(self, overlay: dict[str, Any]) -> Config:
        raw = deep_merge(read_yaml_raw(self.yaml_path), overlay)
        try:
            return build_config(raw, self.yaml_path.parent)
        except ValidationError as e:
            raise ConfigValidationError(_first_error(e)) from None
        except ConfigError as e:
            raise ConfigValidationError(str(e)) from None

    async def effective(self) -> Config:
        return self._build(await self.overlay())

    async def view(self) -> dict[str, Any]:
        overlay = await self.overlay()
        eff = self._build(overlay)
        values = to_section_values(eff.model_dump(mode="json"))
        return {
            "schema": editable_schema(),
            "sections": SECTIONS,
            "values": values,
            "overridden": sorted(p for p in flatten(_to_sections(overlay)) if _visible(p)),
            "pending": await self.pending(eff),
            "readonly": {
                "mode": eff.mode,
                "data_dir": str(eff.data_dir),
                "api": f"{eff.api.host}:{eff.api.port}",
                "config_file": str(self.yaml_path),
            },
        }

    async def pending(self, eff: Config | None = None) -> list[dict[str, str]]:
        eff = eff or await self.effective()
        new = flatten(to_section_values(eff.model_dump(mode="json")))
        cur = flatten(to_section_values(self.running.model_dump(mode="json")))
        out = [
            {"path": p, "apply": "agent"}
            for p in sorted(set(new) | set(cur))
            if new.get(p) != cur.get(p) and apply_of(p) != "live"
        ]
        # 下次开服才用的字段：agent 已采用，但 MC 还没以这些值开过服
        applied = await self._mc_applied()
        if applied is not None:
            for p, v in mc_leaves(self.running).items():
                if applied.get(p) != v and not any(x["path"] == p for x in out):
                    out.append({"path": p, "apply": "mc"})
        return out

    async def _mc_applied(self) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            v = (
                await conn.execute(select(db.kv.c.value).where(db.kv.c.key == MC_APPLIED_KEY))
            ).scalar()
        return v if isinstance(v, dict) else None

    async def mark_mc_started(self) -> None:
        """开服前调用：记下这次开服用的「下次开服生效」字段值。"""
        await self._kv_set(MC_APPLIED_KEY, mc_leaves(self.running))

    async def history(self, limit: int = 50) -> list[dict[str, Any]]:
        h = db.config_history.c
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(h.id, h.ts, h.user, h.action, h.changed, h.note)
                    .order_by(desc(h.id))
                    .limit(limit)
                )
            ).mappings()
            out = []
            for r in rows:
                d = dict(r)
                ts: datetime = d["ts"]
                d["ts"] = (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).isoformat()
                out.append(d)
        return out

    # ---- 写 ----

    async def save(self, values: dict[str, Any], user: str, note: str = "") -> list[dict[str, str]]:
        """values 为面板结构（可只含部分 section）。只把与当前生效值不同的叶子写进覆盖层。"""
        for k in values:
            if k not in {s["key"] for s in SECTIONS}:
                raise ConfigValidationError(f"{k}：未知的配置分组")
        overlay = await self.overlay()
        eff = self._build(overlay)
        cur = flatten(to_section_values(eff.model_dump(mode="json")))
        yaml_flat = flatten(_to_sections(read_yaml_raw(self.yaml_path)))
        incoming = flatten(values)
        flat_over = flatten(_to_sections(overlay))
        changed: list[str] = []
        for p, v in incoming.items():
            if not _visible(p):
                raise ConfigValidationError(f"{p}：该项不能在面板修改")
            if cur.get(p) == v:
                continue
            changed.append(p)
            # 改回与 yaml 相同的值 => 删掉覆盖，保持覆盖层最小
            if yaml_flat.get(p, _MISSING) == v:
                flat_over.pop(p, None)
            else:
                # 父级原来是 None（如 ops.motd: null）时，先去掉父级叶子再写子级
                for q in list(flat_over):
                    if p.startswith(q + ".") or q.startswith(p + "."):
                        flat_over.pop(q)
                flat_over[p] = v
        if not changed:
            return []
        new_overlay = from_section_values(unflatten(flat_over))
        new_eff = self._build(new_overlay)  # 校验失败直接抛出，什么都不写
        await self._commit(new_overlay, user, "save", changed, note)
        self._live(new_eff, changed)
        return [{"path": p, "apply": apply_of(p)} for p in changed]

    async def reset(self, paths: list[str], user: str) -> list[dict[str, str]]:
        overlay = await self.overlay()
        flat_over = flatten(_to_sections(overlay))
        removed = [q for q in flat_over if any(q == p or q.startswith(p + ".") for p in paths)]
        if not removed:
            return []
        for q in removed:
            flat_over.pop(q)
        new_overlay = from_section_values(unflatten(flat_over))
        new_eff = self._build(new_overlay)
        await self._commit(new_overlay, user, "reset", removed, "")
        self._live(new_eff, removed)
        return [{"path": p, "apply": apply_of(p)} for p in removed]

    async def restore(self, history_id: int, user: str) -> list[dict[str, str]]:
        h = db.config_history.c
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(h.overlay).where(h.id == history_id))).scalar()
        if row is None:
            raise KeyError(history_id)
        target: dict[str, Any] = row if isinstance(row, dict) else {}
        before = flatten(_to_sections(await self.overlay()))
        after = flatten(_to_sections(target))
        changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        new_eff = self._build(target)
        await self._commit(target, user, "restore", changed, f"恢复到 #{history_id}")
        self._live(new_eff, changed)
        return [{"path": p, "apply": apply_of(p)} for p in changed]

    async def _commit(
        self, overlay: dict[str, Any], user: str, action: str, changed: list[str], note: str
    ) -> None:
        async with self.engine.begin() as conn:
            stmt = sqlite_insert(db.kv).values(key=OVERLAY_KEY, value=overlay)
            await conn.execute(
                stmt.on_conflict_do_update(index_elements=["key"], set_={"value": overlay})
            )
            await conn.execute(
                insert(db.config_history).values(
                    ts=datetime.now(UTC), user=user, action=action,
                    changed=changed, overlay=overlay, note=note[:200],
                )
            )  # fmt: skip

    async def _kv_set(self, key: str, value: Any) -> None:
        async with self.engine.begin() as conn:
            stmt = sqlite_insert(db.kv).values(key=key, value=value)
            await conn.execute(
                stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value})
            )

    def _live(self, new_eff: Config, changed: list[str]) -> None:
        if self.on_live is not None and any(apply_of(p) == "live" for p in changed):
            self.on_live(new_eff)
            # 立即生效的字段也同步到 running，避免一直显示为待应用
            self.running.security = new_eff.security


_MISSING = object()


def _to_sections(raw: dict[str, Any]) -> dict[str, Any]:
    """yaml / 覆盖层（Config 形状）-> 面板结构，用于和 values 对齐路径。"""
    out = {k: v for k, v in raw.items() if k not in GENERAL_FIELDS}
    general = {k: raw[k] for k in GENERAL_FIELDS if k in raw}
    if general:
        out["general"] = general
    return out


def mc_leaves(cfg: Config) -> dict[str, Any]:
    flat = flatten(to_section_values(cfg.model_dump(mode="json")))
    return {p: v for p, v in flat.items() if apply_of(p) == "mc"}
