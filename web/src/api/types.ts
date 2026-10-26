// Types mirroring the backend API contract (docs/01-design.md §3.8 + task spec).

export interface MeResponse {
  user: 'admin';
}

export interface HealthResponse {
  status: 'ok' | 'degraded';
  version: string;
}

export type ModuleState = 'running' | 'failed' | 'stopped' | 'starting';

export interface ModuleHealth {
  state: ModuleState;
  detail?: string;
  restarts: number;
}

/** M1: observe = 只读观测（生产默认，禁用写操作）；control = 完整控制。 */
export type SystemMode = 'observe' | 'control';

export interface SystemResponse {
  modules: Record<string, ModuleHealth>;
  mode: SystemMode;
}

// --- M1: players / sessions / IP / security ---

/**
 * player.flags 已知值：
 * - "unregistered_whitelisted"：白名单但 EasyAuth 未注册（高危，可被抢注冒用）
 * - "night_owl"：凌晨活跃
 * - "new_account"：新账号
 */
export interface Player {
  name: string;
  first_seen: string | null; // ISO8601
  last_seen: string | null; // ISO8601
  sessions: number;
  last_ip: string | null;
  /** last_ip 是否为启用 PROXY protocol 之后记录的可信 IP。 */
  last_ip_trusted: boolean;
  distinct_ips: number;
  online: boolean;
  whitelisted: boolean;
  /** null = EasyAuth 注册状态未知。 */
  easyauth_registered: boolean | null;
  is_op: boolean;
  flags: string[];
}

export interface PlayersResponse {
  items: Player[];
}

export interface Session {
  id: string;
  name: string;
  login_at: string; // ISO8601
  logout_at: string | null; // ISO8601
  ip: string | null;
  /** false = 2026-10-09 启用 PROXY protocol 前记录的 127.0.0.1，不可信。 */
  ip_trusted: boolean;
  port: number | null;
  leave_reason: string | null;
  authenticated: boolean | null;
  carpet_client: boolean | null;
  fake: boolean;
}

export interface SessionsResponse {
  items: Session[];
}

export interface PlayerIp {
  ip: string;
  sessions: number;
  first_seen: string | null;
  last_seen: string | null;
  trusted: boolean;
}

export interface PlayerDetailResponse {
  player: Player;
  sessions: Session[];
  ips: PlayerIp[];
  events: AppEvent[];
}

export interface IpAccount {
  name: string;
  sessions: number;
}

export interface IpFrpsInfo {
  connections: number;
  by_proxy: Record<string, number>;
  first_seen: string | null; // ISO8601
  last_seen: string | null; // ISO8601
}

export interface IpInfo {
  ip: string;
  first_seen: string | null;
  last_seen: string | null;
  sessions: number;
  accounts: IpAccount[];
  frps: IpFrpsInfo;
  banned: boolean;
}

export type AlertSeverity = 'critical' | 'warning' | 'info';
export type AlertStatus = 'open' | 'acked';

export interface Alert {
  id: string;
  first_ts: string; // ISO8601
  last_ts: string; // ISO8601
  rule: string;
  severity: AlertSeverity;
  title: string;
  detail: string;
  subject: string | null;
  data: Record<string, unknown>;
  status: AlertStatus;
  acked_at: string | null; // ISO8601
  count: number;
}

export interface OpenCounts {
  critical: number;
  warning: number;
  info: number;
}

export interface AlertsResponse {
  items: Alert[];
  open_counts: OpenCounts;
}

export interface AckAllResponse {
  acked: number;
}

export interface Rule {
  id: string;
  name: string;
  description: string;
  severity: AlertSeverity;
  enabled: boolean;
}

export interface RulesResponse {
  items: Rule[];
}

export interface EdgeStatus {
  enabled: boolean;
  connected: boolean;
  last_event_ts: string | null; // ISO8601
  error: string | null;
  events_24h: number;
}

export type ServerState =
  | 'stopped'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'crashed';

export interface PlayersInfo {
  online: number;
  max: number;
  names: string[];
  /** Present when the agent's session tracker is available. */
  real?: string[];
  /** Carpet fake players (shown as "Anonymous Player" in the server-list sample). */
  fake?: string[];
  real_count?: number;
  fake_count?: number;
}

export interface ServerStatus {
  state: ServerState;
  pid: number | null;
  uptime_s: number | null;
  mc_version: string | null;
  players: PlayersInfo | null;
}

export interface AcceptedResponse {
  accepted: true;
}

export interface CommandResponse {
  output: string;
}

export type EventType =
  | 'server_ready'
  | 'server_stopping'
  | 'player_login'
  | 'player_leave'
  | 'auth_attempt'
  | 'auth_success'
  | 'chat'
  | 'ban_ip'
  | 'anticheat'
  | (string & {});

export interface AppEvent {
  id: number;
  ts: string; // ISO8601
  type: EventType;
  source: string;
  data: Record<string, unknown>;
}

export interface EventsResponse {
  items: AppEvent[];
}

// WebSocket /api/ws/console messages (server -> client).
export interface WsLogMessage {
  kind: 'log';
  ts: string;
  line: string;
}

export interface WsEventMessage {
  kind: 'event';
  event: AppEvent;
}

export interface WsStatusMessage {
  kind: 'status';
  status: ServerStatus;
}

export interface WsCommandResultMessage {
  kind: 'command_result';
  command: string;
  output: string;
}

// M1: a security alert pushed over the console socket.
export interface WsAlertMessage {
  kind: 'alert';
  alert: Alert;
}

export type WsServerMessage =
  | WsLogMessage
  | WsEventMessage
  | WsStatusMessage
  | WsCommandResultMessage
  | WsAlertMessage;

// Client -> server.
export interface WsCommandMessage {
  kind: 'command';
  command: string;
}

// --- M2: QQ bot / whitelist ---

export interface BotGroup {
  openid_masked: string;
  added_at: string | null; // ISO8601
  alert_group: boolean;
}

export interface BotMaintenance {
  active: boolean;
  until: string | null; // ISO8601
}

export interface BotMonitor {
  last_online: boolean | null;
  fail_count: number;
}

export interface BotStatus {
  enabled: boolean;
  connected: boolean;
  robot_name: string | null;
  last_error: string | null;
  groups: BotGroup[];
  pushes_today: number;
  maintenance: BotMaintenance;
  monitor: BotMonitor;
}

export interface Application {
  name: string;
  openid_masked: string;
  ts: string; // ISO8601
}

export interface ApplicationsResponse {
  items: Application[];
}

export type GrantStatus = 'pending' | 'activated' | 'expired' | 'cancelled';

/** 经申请获批的白名单：期限内须进服并完成注册，否则自动移出 */
export interface WhitelistGrant {
  name: string;
  openid_masked: string;
  granted_at: string; // ISO8601
  deadline: string; // ISO8601
  status: GrantStatus;
  resolved_at: string | null;
}

export interface GrantsResponse {
  activation_hours: number; // 0 = 不限期
  items: WhitelistGrant[]; // 最新获批在前
}

export interface Interaction {
  id: number;
  ts: string; // ISO8601
  group_masked: string;
  openid_masked: string;
  admin: boolean;
  cmd: string;
  input: string;
  output: string;
}

export interface InteractionsResponse {
  items: Interaction[];
}

/** Bot-style reply text, shown verbatim. Prefix encodes outcome: ✅/⚠️/❌. */
export interface BotReplyResponse {
  reply: string;
}

export interface SimulateResponse {
  cmd: string;
  reply: string;
}

export interface WhitelistEntry {
  name: string;
  uuid: string;
  /** null = EasyAuth 注册状态未知。 */
  easyauth_registered: boolean | null;
  /** 绑定的 QQ 成员 openid（掩码形式）。 */
  members: string[];
}

export interface WhitelistResponse {
  items: WhitelistEntry[];
}

// --- M3: 运维任务 / 备份 -------------------------------------------------------

/** 可触发的运维任务。schedule=null 表示没有固定计划（如按需备份）。 */
export interface Job {
  id: 'scheduled_restart' | 'backup' | (string & {});
  name: string;
  schedule: string | null; // 例如 "05:00"
  next_run: string | null; // ISO8601
}

export type JobRunStatus = 'running' | 'ok' | 'failed' | 'interrupted';

/** 一次任务运行的历史记录。 */
export interface JobRun {
  id: number;
  job: string;
  trigger: string;
  started_at: string; // ISO8601
  finished_at: string | null; // ISO8601；running 时为 null
  status: JobRunStatus;
  log: string; // 多行文本
  result: Record<string, unknown>;
}

export interface WatchdogStatus {
  /** 自动重启被暂停（看门狗熔断），需人工恢复。 */
  suspended: boolean;
  recent_crashes: number;
}

export interface JobProgress {
  label: string;
  done_bytes: number;
  total_bytes: number;
  /** 0–100；总量未知时为 null。 */
  percent: number | null;
  /** 最近 30 秒的平均速度（字节/秒）。 */
  rate_bps: number | null;
  /** 预计剩余秒数。 */
  eta_s: number | null;
  elapsed_s: number;
}

/** 正在运行的任务：实时日志（运行记录要等结束才落库）与当前步骤的进度。 */
export interface LiveJob {
  job: string;
  run_id: number;
  started_at: string;
  log: string;
  progress: JobProgress | null;
}

export interface JobsResponse {
  jobs: Job[];
  /** 正在运行的任务 id；无任务运行时为 null。 */
  running: string | null;
  live?: LiveJob | null;
  watchdog: WatchdogStatus;
  history: JobRun[];
}

/** 世界地图（mapgen）一次生成的记录 */
export interface MapRun {
  started_at: string;
  finished_at: string;
  seconds: number;
  trigger: string;
  ok: boolean;
  error: string | null;
  result: {
    snapshot?: { regions: number; copied: number; removed: number };
    render?: {
      regions?: number;
      rendered?: Record<string, number>;
      tiles_written?: number;
      levels?: number;
      hot_tiles?: number;
      tiles?: Record<string, number>;
      bytes?: number;
      seconds?: number;
    };
    version?: string;
  } | null;
}

export interface MapStatus {
  enabled: boolean;
  mode?: 'restart' | 'time';
  schedule?: string | null;
  fallback_at?: string;
  running?: boolean;
  phase?: string | null;
  started_at?: string | null;
  next_run?: string | null;
  pushed?: boolean;
  last?: MapRun | null;
  history?: MapRun[];
  last_error?: string | null;
  tiles?: { levels: number; generated_at: string | null; counts: Record<string, number> } | null;
  public_url?: string | null;
  has_preview?: boolean;
  hot_minutes?: number;
  announce?: boolean;
}

export interface JobRunAccepted {
  accepted: true;
}

export interface WatchdogResetResponse {
  ok: true;
}

export interface Backup {
  name: string;
  size: number; // 字节
  mtime: string; // ISO8601
}

export interface BackupsResponse {
  items: Backup[]; // 最新在前
  /** 面板下载限速（KiB/s），0 = 不限速。 */
  download_rate_kib?: number;
}

export interface ResticSnapshot {
  id: string;
  time: string; // ISO8601
  tags: string[];
  total_bytes: number | null;
  added_bytes: number | null;
}

/** 快照仓库：本机，或异地副本（家里 NAS / 对象存储）。 */
export type ResticRepoId = 'local' | 'nas' | 's3';

/** 回退范围：只回退世界，或世界 + Mod + 配置 + 名单类文件。 */
export type RestoreScope = 'world' | 'game';

export interface ResticRepo {
  id: ResticRepoId;
  name: string;
}

export interface ResticInfo {
  enabled: boolean;
  /** 当前列出的是哪个仓库（旧后端返回本机仓库路径）。 */
  repo?: string;
  /** 可选仓库；只有本机时不显示切换。 */
  repos?: ResticRepo[];
  /** 正在运行的运维任务（回退期间禁用按钮）。 */
  restore_running?: string | null;
  /** 停服前的游戏内倒计时（分钟）。 */
  warn_minutes?: number;
  error?: string;
  snapshots: ResticSnapshot[]; // 最新在前
  stats: { repo_bytes: number; uncompressed_bytes: number | null } | null;
}

// --- M4: 通知设置 -------------------------------------------------------------
// Web 面板铃铛始终收到全部告警；邮件 / QQ 两个渠道按级别额外推送。

/** 告警级别（界面中文：严重/警告/提示）。 */
export type Sev = 'critical' | 'warning' | 'info';

export type EmailSecurity = 'ssl' | 'starttls';

/** 邮件渠道配置。password 从不回显，只有 has_password 表示是否已设置。 */
export interface NotifyEmailConfig {
  enabled: boolean;
  host: string;
  port: number;
  security: EmailSecurity;
  username: string;
  sender: string;
  to: string[];
  severities: Sev[];
  max_per_hour: number;
  has_password: boolean;
}

export interface NotifyQqGroup {
  openid: string;
  label: string;
}

/** QQ 渠道配置。 */
export interface NotifyQqConfig {
  enabled: boolean;
  severities: Sev[];
  groups: NotifyQqGroup[];
}

/** 一条告警规则：default_severity 为规则默认级别，severity 为用户覆盖（null=默认）。 */
export interface NotifyRule {
  id: string;
  name: string;
  description: string;
  default_severity: Sev;
  enabled: boolean;
  severity: Sev | null;
  /** 当前环境收不到这条规则依赖的信号、被强制停用时的原因。 */
  unavailable?: string | null;
}

/** 机器人已知的群（可勾选为告警群）。 */
export interface NotifyKnownGroup {
  openid: string;
  openid_masked: string;
  added_at: string | null; // ISO8601
  is_alert: boolean;
}

export interface NotifyBotState {
  enabled: boolean;
  connected: boolean;
}

export interface NotifyStats {
  email_sent_hour: number;
  email_dropped: number;
  qq_sent_today: number;
  last_error: {
    email: string | null;
    qq: string | null;
  };
}

/** GET /api/settings/notify 的完整响应。 */
export interface NotifySettings {
  email: NotifyEmailConfig;
  qq: NotifyQqConfig;
  rules: NotifyRule[];
  known_groups: NotifyKnownGroup[];
  bot: NotifyBotState;
  stats: NotifyStats;
}

/**
 * PUT /api/settings/notify 的请求体。
 * password: 省略或 null = 不修改；"" = 清除；非空 = 设置新密码（只写，从不回显）。
 */
export interface NotifySettingsUpdate {
  email: {
    enabled: boolean;
    host: string;
    port: number;
    security: EmailSecurity;
    username: string;
    sender: string;
    to: string[];
    severities: Sev[];
    max_per_hour: number;
    password?: string | null;
  };
  qq: {
    enabled: boolean;
    severities: Sev[];
    groups: NotifyQqGroup[];
  };
  rules: Record<string, { enabled: boolean; severity: Sev | null }>;
}

export type NotifyChannel = 'email' | 'qq';

export interface NotifyTestResult {
  ok: boolean;
  detail: string;
}
