"""配置加载：非机密的 mcops.yaml + 机密的 secrets.yaml（chmod 600，不入库）。

所有路径都来自配置；代码里不出现任何生产机硬编码路径。
"""

from __future__ import annotations

import os
import stat
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class _Strict(BaseModel):
    # use_attribute_docstrings：字段下方的 """说明""" 进 JSON Schema 的 description，面板直接展示
    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class ServerConfig(_Strict):
    """Minecraft 服务端托管配置。"""

    backend: Literal["subprocess", "systemd"] = "subprocess"
    """subprocess：agent 直接拉起 java（本地开发）；systemd：托管 mc-server.service（生产）。"""
    game_dir: Path
    java_bin: str = "java"
    java_opts: list[str] = Field(default_factory=lambda: ["-Xms1G", "-Xmx2G"])
    jar: str
    systemd_unit: str = "mc-server.service"
    systemd_user: bool = True
    """用 `systemctl --user` 管理（配合 loginctl enable-linger），agent 无需 root。"""
    manage_unit: bool = False
    """开服前按 java_bin / java_opts / jar 生成 systemd 单元（用户级），面板改 JVM 参数才会生效。
    加固项等放在 <unit>.d/ drop-in 里，不受影响。"""
    port: int = Field(default=25565, ge=1, le=65535)
    rcon_host: str = "127.0.0.1"
    rcon_port: int = Field(default=25575, ge=1, le=65535)
    easyauth_db: Path | None = None
    """EasyAuth SQLite 数据库，默认 <game_dir>/EasyAuth/easyauth.db（只读）。"""
    start_ready_timeout_s: float = Field(default=180.0, gt=0, le=3600)
    stop_timeout_s: float = Field(default=60.0, gt=0, le=600)

    @property
    def latest_log(self) -> Path:
        return self.game_dir / "logs" / "latest.log"


class SecurityRulesConfig(_Strict):
    night_start_hour: int = Field(default=1, ge=0, le=23)
    night_end_hour: int = Field(default=6, ge=0, le=23)
    ip_allowlist: list[str] = Field(default_factory=list)
    """自己的 IP（不触发新 IP / 多账号 / SSH 激增规则）。"""
    disabled_rules: list[str] = Field(default_factory=list)
    elytra_burst_count: int = Field(default=15, ge=3, le=500)
    """鞘翅飞行中 moved too quickly 达到多少次才告警（生产实测正常飞行 5 分钟最多 8 次）。"""
    elytra_burst_window_minutes: int = Field(default=5, ge=1, le=60)


class EdgePort(_Strict):
    """frps 对公网开放的一个端口（只用于面板「网络」页展示）。"""

    port: int = Field(ge=1, le=65535)
    proxy: str = Field(min_length=1, max_length=64)  # frpc 里的代理名，如 minecraft / ssh
    desc: str = Field(default="", max_length=64)


class EdgeConfig(_Strict):
    """云主机 frps 日志采集（受限只读 SSH）。"""

    host: str
    user: str = "mcops-ro"
    port: int = Field(default=22, ge=1, le=65535)
    key_file: Path
    known_hosts: Path
    retention_days: int = Field(default=180, ge=1, le=3650)
    public_ports: list[EdgePort] = Field(
        default_factory=lambda: [
            EdgePort(port=25565, proxy="minecraft", desc="Minecraft"),
            EdgePort(port=7000, proxy="frp", desc="frp 控制连接"),
        ]
    )


class SmtpConfig(_Strict):
    host: str
    port: int = 465
    sender: str
    username: str | None = None
    to: list[str]
    severities: list[str] = Field(default_factory=lambda: ["critical", "warning"])


class BotConfig(_Strict):
    """QQ 机器人（M2）。enabled=false 时只提供面板里的指令模拟器，不连 QQ。"""

    enabled: bool = False
    name: str = Field(default="Minecraft 服务器 bot", min_length=1, max_length=40)
    """帮助菜单第一行显示的机器人名字。"""
    display_host: str
    join_note: str = ""
    admin_openids: list[str] = Field(default_factory=list)
    """管理员 member_openid。留空 => 无人是管理员（fail closed）。"""
    allowed_groups: list[str] = Field(default_factory=list)
    alert_groups: list[str] = Field(default_factory=list)
    """接收严重安全告警的管理群 group_openid。留空 => 不在 QQ 推送安全告警。"""
    max_pending_per_user: int = Field(default=2, ge=1, le=20)
    max_pending_total: int = Field(default=20, ge=1, le=500)
    monitor_interval: float = Field(default=60.0, gt=0, le=3600)
    fail_threshold: int = Field(default=3, ge=1, le=20)
    active_alert: bool = True
    planned_actors: list[str] = Field(default_factory=lambda: ["mc-restart", "cmd_stop"])
    activation_hours: int = Field(default=48, ge=0, le=720)
    """经申请获批后，须在这么多小时内进服并完成注册，否则自动移出白名单。0 表示不限。"""
    frp_server_port: int = Field(default=7000, ge=0, le=65535)
    """frps 监听端口（frpc 连接的远端端口），「状态」据此读隧道延迟。0 表示不显示。"""
    # 过渡期（observe 模式）兼容旧脚本的文件契约
    legacy_mc_sh: Path | None = None
    watchdog_state_file: Path | None = None
    maintenance_signal_file: Path | None = None
    maintenance_cooldown: float = 300.0
    legacy_bot_dir: Path | None = None
    """旧 mc-qq-bot 目录，首次启动时导入其申请 / 成员 / 群 / 日志（只读）。"""


class MotdSettings(_Strict):
    server_list: str | None = None
    """server.properties 的 motd="""
    line1: str | None = None
    """MiniMOTD 第一行"""
    line2: str | None = None
    """MiniMOTD 第二行，{version} 替换为 jar 名里的 MC 版本"""


class OffsiteSettings(_Strict):
    """异地副本。凭据（S3 密钥、NAS 账号密码）在「账号与安全 → 机密」里填。"""

    s3_repo: str = Field(default="", max_length=500, pattern=r"^(s3:https?://\S+)?$")
    """S3 兼容存储的 restic 地址，如 s3:https://cos.ap-shanghai.myqcloud.com/<存储桶>/mc-ops。留空不启用。"""
    smb_host: str = Field(default="", max_length=253, pattern=r"^[A-Za-z0-9.:_-]*$")
    """局域网 NAS 的 IP 或主机名（SMB「网络邻居」）。留空不启用。"""
    smb_share: str = Field(default="", max_length=200)
    """共享名（NAS 里开放给网络邻居的那个文件夹）。"""
    smb_path: str = Field(default="mc-ops-restic", max_length=200)
    """共享里存放仓库的子目录。"""
    rclone_binary: str = "rclone"
    """访问 NAS 用的 rclone（restic 经它读写 SMB）。"""
    limit_upload_kib: int = Field(default=512, ge=0, le=1_000_000)
    """上传限速，0 = 不限。上传和玩家共用上行带宽。"""


class ResticSettings(_Strict):
    """M4 restic 增量热备份。密码在 secrets.yaml 的 restic_password。"""

    enabled: bool = False
    binary: str = "restic"
    repo: str = ""
    """本地路径或 restic 支持的远端 URL；默认 <game_dir>/../restic-repo。"""
    interval_minutes: int = Field(default=360, ge=15, le=10080)
    """默认每 6 小时一次（每天 4 个快照）。"""
    keep_hourly: int = Field(default=4, ge=0, le=1000)
    keep_daily: int = Field(default=14, ge=0, le=1000)
    keep_weekly: int = Field(default=8, ge=0, le=1000)
    keep_monthly: int = Field(default=6, ge=0, le=1000)
    offsite: OffsiteSettings = Field(default_factory=OffsiteSettings)
    """异地副本：每次本地快照后，把新快照复制到下面配置了的仓库（可同时配两个）。"""


class OpsSettings(_Strict):
    """M3 运维任务（control 模式生效）。"""

    autostart: bool = True
    """agent 启动时若服务端没在跑且未被手动关停，则自动启动（主机重启后自动开服）。"""
    restart_time: str | None = Field(default="05:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    """每日定时重启（本地时间 HH:MM），null 关闭。替代 crontab 里的 mc-restart.sh。"""
    warn_minutes: int = Field(default=5, ge=1, le=60)
    crash_threshold: int = Field(default=3, ge=1, le=20)
    crash_window_minutes: int = Field(default=10, ge=1, le=1440)
    backup_dir: Path | None = None
    """默认 <game_dir>/../backups（与旧脚本相同目录，旧的 restore 仍可用）。"""
    backup_keep_days: int = Field(default=7, ge=1, le=3650)
    backup_min_keep: int = Field(default=3, ge=1, le=100)
    backup_exclude: list[str] = Field(default_factory=lambda: ["logs", "crash-reports"])
    spawn: tuple[int, int, int] | None = None
    """每次开服就绪后执行 setworldspawn x y z。"""
    motd: MotdSettings | None = None
    """每次开服前同步到 server.properties / MiniMOTD。"""
    restic: ResticSettings = Field(default_factory=ResticSettings)
    flight_watch: bool = True
    """RCON 轮询检测悬空/飞行，只告警不踢人（配合 server.properties allow-flight=true）。"""
    flight_interval_s: float = Field(default=3.0, gt=0, le=60)
    flight_samples: int = Field(default=5, ge=2, le=60)
    download_rate_kib: int = Field(default=512, ge=0, le=1_000_000)
    """面板下载备份的限速（KiB/s，0 = 不限速）。下载经云主机转发，和玩家共用出口带宽。"""


class ApiConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = 8765
    static_dir: Path | None = None
    """前端构建产物目录（web/dist）。为空则不提供静态文件。"""
    allowed_origins: list[str] = Field(default_factory=list)
    """WebSocket / 写请求允许的 Origin。为空时只允许与 Host 同源。"""
    session_ttl_s: int = Field(default=12 * 3600, ge=300, le=30 * 86400)
    cookie_secure: bool = False
    """经 SSH 隧道访问 http://127.0.0.1 时必须为 False，否则浏览器不回传 cookie。"""

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        # 设计约束（docs/01-design.md §3.8）：面板只监听回环地址。
        if v not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("api.host 只允许回环地址（127.0.0.1 / ::1），面板不对外暴露")
        return v


class Secrets(_Strict):
    rcon_password: SecretStr | None = None
    """observe 模式下可不配置（生产 M1 不开 RCON）。"""
    smtp_password: SecretStr | None = None
    qq_appid: SecretStr | None = None
    qq_secret: SecretStr | None = None
    admin_password_hash: SecretStr
    """argon2 哈希，用 `mcops hash-password` 生成。"""
    totp_secret: SecretStr | None = None
    restic_password: SecretStr | None = None
    offsite_s3_access_key: SecretStr | None = None
    offsite_s3_secret_key: SecretStr | None = None
    offsite_smb_user: SecretStr | None = None
    offsite_smb_password: SecretStr | None = None


class RetentionConfig(_Strict):
    """本地数据库的保留期（天）。每天清理一次，0 表示永久保留。frps 连接记录沿用 edge.retention_days。"""

    events_days: int = Field(default=365, ge=0)
    bot_interactions_days: int = Field(default=90, ge=0)
    job_runs_days: int = Field(default=365, ge=0)


class Config(_Strict):
    mode: Literal["observe", "control"] = "control"
    """observe：只读旁路观测（M1 生产），不启停服务端、不发命令；control：完全托管。"""
    data_dir: Path
    server: ServerConfig
    security: SecurityRulesConfig = Field(default_factory=SecurityRulesConfig)
    edge: EdgeConfig | None = None
    smtp: SmtpConfig | None = None
    bot: BotConfig | None = None
    ops: OpsSettings = Field(default_factory=OpsSettings)
    api: ApiConfig = Field(default_factory=ApiConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    timezone: str | None = None
    """时区（IANA 名，如 Asia/Shanghai），用于定时任务、凌晨规则和图表；留空跟随系统。"""
    secrets_file: Path
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"未知时区：{v}") from None
        return v or None

    def tzinfo(self) -> tzinfo:
        if self.timezone:
            from zoneinfo import ZoneInfo

            return ZoneInfo(self.timezone)
        tz = datetime.now().astimezone().tzinfo
        assert tz is not None
        return tz

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mcops.db"


class LoadedConfig(BaseModel):
    """运行期使用的完整配置（含机密）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: Config
    secrets: Secrets


class ConfigError(RuntimeError):
    pass


def _resolve(base: Path, p: Path) -> Path:
    return p if p.is_absolute() else (base / p).resolve()


def read_yaml_raw(path: str | os.PathLike[str]) -> dict[str, Any]:
    cfg_path = Path(path).resolve()
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as e:
        raise ConfigError(f"配置文件不存在：{cfg_path}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件顶层必须是映射：{cfg_path}")
    return raw


def load_config(
    path: str | os.PathLike[str], overlay: dict[str, Any] | None = None
) -> LoadedConfig:
    """读 mcops.yaml（默认值），叠加面板保存的覆盖层（数据库里的 config.overlay），校验并解析路径。"""
    cfg_path = Path(path).resolve()
    raw = deep_merge(read_yaml_raw(cfg_path), overlay or {})
    cfg = build_config(raw, cfg_path.parent)
    secrets = load_secrets(cfg.secrets_file)
    return LoadedConfig(config=cfg, secrets=secrets)


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """over 覆盖 base；dict 递归合并，其它类型（含 list）整体替换。不修改入参。"""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def build_config(raw: dict[str, Any], base: Path) -> Config:
    cfg = Config.model_validate(raw)
    # 相对路径一律相对配置文件所在目录解析，避免依赖进程 cwd。
    cfg.data_dir = _resolve(base, cfg.data_dir)
    cfg.secrets_file = _resolve(base, cfg.secrets_file)
    cfg.server.game_dir = _resolve(base, cfg.server.game_dir)
    if cfg.api.static_dir is not None:
        cfg.api.static_dir = _resolve(base, cfg.api.static_dir)
    cfg.server.jar = _resolve_jar(cfg.server.game_dir, cfg.server.jar)
    cfg.server.easyauth_db = _resolve(
        base, cfg.server.easyauth_db or cfg.server.game_dir / "EasyAuth" / "easyauth.db"
    )
    if not cfg.ops.restic.repo:
        cfg.ops.restic.repo = str(cfg.server.game_dir.parent / "restic-repo")
    cfg.ops.backup_dir = _resolve(
        base, cfg.ops.backup_dir or cfg.server.game_dir.parent / "backups"
    )
    if cfg.bot is not None:
        for attr in (
            "legacy_mc_sh",
            "watchdog_state_file",
            "maintenance_signal_file",
            "legacy_bot_dir",
        ):
            v = getattr(cfg.bot, attr)
            if v is not None:
                setattr(cfg.bot, attr, _resolve(base, v))
    if cfg.edge is not None:
        cfg.edge.key_file = _resolve(base, cfg.edge.key_file)
        cfg.edge.known_hosts = _resolve(base, cfg.edge.known_hosts)
    return cfg


def _resolve_jar(game_dir: Path, jar: str) -> str:
    """jar 可写成通配符（如 fabric-server-mc.*.jar）：恰好匹配一个就用它；一个都没有说明还没装
    （首次安装向导会装好），保留通配符原样；匹配到多个是歧义，报错。"""
    if not any(ch in jar for ch in "*?["):
        return jar
    matches = sorted(p.name for p in game_dir.glob(jar)) if game_dir.is_dir() else []
    if not matches:
        return jar
    if len(matches) != 1:
        raise ConfigError(f"server.jar 通配符 {jar!r} 在 {game_dir} 匹配到 {len(matches)} 个文件")
    return matches[0]


def load_secrets(path: Path) -> Secrets:
    try:
        st = path.stat()
    except FileNotFoundError as e:
        raise ConfigError(f"机密文件不存在：{path}") from e
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"机密文件权限过宽（应为 600）：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Secrets.model_validate(raw)


def update_secrets(path: Path, changes: dict[str, str | None]) -> None:
    """改写 secrets.yaml 的若干键（None = 删除），原子替换并保持 600。其余键原样保留。"""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"机密文件格式不对：{path}")
    for k, v in changes.items():
        if v is None:
            raw.pop(k, None)
        else:
            raw[k] = v
    Secrets.model_validate(raw)  # 写之前先校验，避免写坏导致 agent 起不来
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
