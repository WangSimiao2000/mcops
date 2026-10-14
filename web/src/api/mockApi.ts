import { ApiError } from './http';
import {
  mockCapabilities,
  mockChangePassword,
  mockConfig,
  mockHistory,
  mockProperties,
  mockResetConfig,
  mockRestartAgent,
  mockRestoreHistory,
  mockSecrets,
  mockTotpConfirm,
  mockTotpDisable,
  mockTotpSetup,
  mockTotpStatus,
  mockUpdateConfig,
  mockUpdateProperties,
  mockUpdateSecrets,
} from './configMock';
import type {
  Capabilities,
  ConfigHistoryResponse,
  ConfigResponse,
  ConfigUpdateResult,
  ConfigValues,
  PropertiesResponse,
  PropertiesUpdateResult,
  RestartAgentResult,
  SecretsResponse,
  SecretsUpdateResult,
  TotpSetupResponse,
  TotpStatus,
} from './config';
import type {
  AcceptedResponse,
  AckAllResponse,
  Alert,
  AlertStatus,
  AlertsResponse,
  AppEvent,
  Application,
  ApplicationsResponse,
  GrantsResponse,
  Backup,
  BackupsResponse,
  ResticInfo,
  ResticRepoId,
  BotReplyResponse,
  BotStatus,
  CommandResponse,
  EdgeStatus,
  EventsResponse,
  HealthResponse,
  Interaction,
  InteractionsResponse,
  IpInfo,
  Job,
  JobRun,
  JobRunAccepted,
  JobsResponse,
  MeResponse,
  NotifyChannel,
  NotifySettings,
  NotifySettingsUpdate,
  NotifyTestResult,
  OpenCounts,
  Player,
  PlayerDetailResponse,
  PlayersResponse,
  RulesResponse,
  ServerState,
  ServerStatus,
  Session,
  SessionsResponse,
  SimulateResponse,
  SystemResponse,
  WatchdogResetResponse,
  WhitelistEntry,
  WhitelistResponse,
} from './types';

// In-memory mock backend shared by the mock API client and the mock socket.
// It is intentionally simple: enough to exercise every page end to end.

const MOCK_PASSWORD = 'admin';

const state = {
  loggedIn: false,
  server: {
    state: 'running' as ServerState,
    pid: 48213,
    uptime_s: 3 * 3600 + 1247,
    mc_version: '1.21.11',
    players: {
      online: 3,
      max: 20,
      names: ['OpAdmin', 'Zoe', 'Sandy'],
      real: ['OpAdmin', 'Zoe'],
      fake: ['Sandy'],
      real_count: 2,
      fake_count: 1,
    },
  } satisfies ServerStatus,
};

let eventSeq = 1000;
const now = () => new Date().toISOString();

const seedEvents: AppEvent[] = [
  { id: 1, ts: now(), type: 'server_ready', source: 'server', data: { mc_version: '1.21.11' } },
  {
    id: 2,
    ts: now(),
    type: 'player_login',
    source: 'logs',
    data: { name: 'OpAdmin', ip: '203.0.113.17', port: 54122, entity_id: 215, pos: [12, 64, -8] },
  },
  {
    id: 3,
    ts: now(),
    type: 'player_login',
    source: 'logs',
    data: { name: 'Zoe', ip: '198.51.100.42', port: 51880, entity_id: 216, pos: [40, 70, 15] },
  },
  { id: 4, ts: now(), type: 'auth_success', source: 'easyauth', data: { name: 'OpAdmin' } },
  { id: 5, ts: now(), type: 'chat', source: 'logs', data: { name: 'OpAdmin', message: '测试一下聊天' } },
  {
    id: 6,
    ts: now(),
    type: 'anticheat',
    source: 'logs',
    data: { name: 'Scout42', kind: 'moved_too_quickly', detail: 'moved too quickly! 42.1,0,0' },
  },
];

const events: AppEvent[] = [...seedEvents];

export function mockPushEvent(ev: Omit<AppEvent, 'id' | 'ts'> & Partial<Pick<AppEvent, 'ts'>>): AppEvent {
  eventSeq += 1;
  const full: AppEvent = { id: eventSeq, ts: ev.ts ?? now(), type: ev.type, source: ev.source, data: ev.data };
  events.push(full);
  return full;
}

export function mockServerStatus(): ServerStatus {
  return { ...state.server, players: state.server.players ? { ...state.server.players } : null };
}

export function mockSetServerState(s: ServerState): void {
  state.server = { ...state.server, state: s };
}

const delay = (ms = 150) => new Promise<void>((r) => setTimeout(r, ms));

// --- M1 mock dataset ---------------------------------------------------------
// Fictional player names + RFC 5737 documentation IPs (203.0.113.x / 198.51.100.x).
// 127.0.0.1 appears only as pre-PROXY (ip_trusted=false) history.

const T = (iso: string) => iso;

const mockPlayers: Player[] = [
  {
    name: 'Zoe',
    first_seen: T('2026-09-20T12:03:00Z'),
    last_seen: T('2026-10-08T15:40:00Z'),
    sessions: 14,
    last_ip: '203.0.113.24',
    last_ip_trusted: true,
    distinct_ips: 2,
    online: false,
    whitelisted: true,
    easyauth_registered: false,
    is_op: false,
    flags: ['unregistered_whitelisted'],
  },
  {
    name: 'EveX',
    first_seen: T('2026-09-18T08:10:00Z'),
    last_seen: T('2026-10-07T22:12:00Z'),
    sessions: 9,
    last_ip: '198.51.100.8',
    last_ip_trusted: true,
    distinct_ips: 1,
    online: false,
    whitelisted: true,
    easyauth_registered: false,
    is_op: false,
    flags: ['unregistered_whitelisted'],
  },
  {
    name: 'OpAdmin',
    first_seen: T('2026-08-01T06:00:00Z'),
    last_seen: T('2026-10-09T03:30:00Z'),
    sessions: 132,
    last_ip: '203.0.113.17',
    last_ip_trusted: true,
    distinct_ips: 3,
    online: true,
    whitelisted: true,
    easyauth_registered: true,
    is_op: true,
    flags: [],
  },
  {
    name: 'LiuChen',
    first_seen: T('2026-08-15T10:30:00Z'),
    last_seen: T('2026-10-09T02:50:00Z'),
    sessions: 47,
    last_ip: '198.51.100.42',
    last_ip_trusted: true,
    distinct_ips: 2,
    online: true,
    whitelisted: true,
    easyauth_registered: true,
    is_op: false,
    flags: [],
  },
  {
    name: 'Scout42',
    first_seen: T('2026-10-04T02:41:00Z'),
    last_seen: T('2026-10-04T02:58:00Z'),
    sessions: 2,
    last_ip: '203.0.113.96',
    last_ip_trusted: true,
    distinct_ips: 2,
    online: false,
    whitelisted: false,
    easyauth_registered: true,
    is_op: false,
    flags: ['new_account', 'night_owl'],
  },
  {
    name: 'Yuki_Y',
    first_seen: T('2026-07-11T13:00:00Z'),
    last_seen: T('2026-10-02T19:22:00Z'),
    sessions: 61,
    last_ip: '127.0.0.1',
    last_ip_trusted: false,
    distinct_ips: 4,
    online: false,
    whitelisted: true,
    easyauth_registered: true,
    is_op: false,
    flags: [],
  },
];

const mockSessionsByPlayer: Record<string, Session[]> = {
  Zoe: [
    {
      id: 's-zz-3', name: 'Zoe', login_at: T('2026-10-08T14:50:00Z'), logout_at: T('2026-10-08T15:40:00Z'),
      ip: '203.0.113.24', ip_trusted: true, port: 54310, leave_reason: 'disconnected',
      authenticated: true, carpet_client: false, fake: false,
    },
    {
      id: 's-zz-2', name: 'Zoe', login_at: T('2026-09-28T09:12:00Z'), logout_at: T('2026-09-28T10:02:00Z'),
      ip: '127.0.0.1', ip_trusted: false, port: null, leave_reason: 'timed out',
      authenticated: true, carpet_client: false, fake: false,
    },
  ],
  OpAdmin: [
    {
      id: 's-mm-1', name: 'OpAdmin', login_at: T('2026-10-09T03:00:00Z'), logout_at: null,
      ip: '203.0.113.17', ip_trusted: true, port: 54122, leave_reason: null,
      authenticated: true, carpet_client: true, fake: false,
    },
    {
      id: 's-mm-0', name: 'OpAdmin', login_at: T('2026-10-08T20:00:00Z'), logout_at: T('2026-10-08T23:11:00Z'),
      ip: '127.0.0.1', ip_trusted: false, port: null, leave_reason: 'disconnected',
      authenticated: true, carpet_client: true, fake: false,
    },
  ],
  Scout42: [
    {
      id: 's-v-1', name: 'Scout42', login_at: T('2026-10-04T02:41:00Z'), logout_at: T('2026-10-04T02:47:00Z'),
      ip: '203.0.113.96', ip_trusted: true, port: 51002, leave_reason: 'kicked: flying is not enabled',
      authenticated: false, carpet_client: false, fake: false,
    },
    {
      id: 's-v-2', name: 'Scout42', login_at: T('2026-10-04T02:52:00Z'), logout_at: T('2026-10-04T02:58:00Z'),
      ip: '203.0.113.112', ip_trusted: true, port: 51044, leave_reason: 'disconnected',
      authenticated: true, carpet_client: false, fake: false,
    },
  ],
};

const mockIpsByPlayer: Record<string, PlayerDetailResponse['ips']> = {
  Zoe: [
    { ip: '203.0.113.24', sessions: 12, first_seen: T('2026-09-30T00:00:00Z'), last_seen: T('2026-10-08T15:40:00Z'), trusted: true },
    { ip: '127.0.0.1', sessions: 2, first_seen: T('2026-09-20T12:03:00Z'), last_seen: T('2026-09-28T10:02:00Z'), trusted: false },
  ],
  OpAdmin: [
    { ip: '203.0.113.17', sessions: 90, first_seen: T('2026-09-30T00:00:00Z'), last_seen: T('2026-10-09T03:30:00Z'), trusted: true },
    { ip: '127.0.0.1', sessions: 42, first_seen: T('2026-08-01T06:00:00Z'), last_seen: T('2026-09-29T23:00:00Z'), trusted: false },
  ],
  Scout42: [
    { ip: '203.0.113.96', sessions: 1, first_seen: T('2026-10-04T02:41:00Z'), last_seen: T('2026-10-04T02:47:00Z'), trusted: true },
    { ip: '203.0.113.112', sessions: 1, first_seen: T('2026-10-04T02:52:00Z'), last_seen: T('2026-10-04T02:58:00Z'), trusted: true },
  ],
};

const mockIpInfo: Record<string, IpInfo> = {
  '203.0.113.96': {
    ip: '203.0.113.96',
    first_seen: T('2026-10-04T02:41:00Z'),
    last_seen: T('2026-10-04T02:58:00Z'),
    sessions: 3,
    accounts: [
      { name: 'Scout42', sessions: 2 },
      { name: 'Yuki_Y', sessions: 1 },
    ],
    frps: {
      connections: 58,
      by_proxy: { minecraft: 12, ssh: 46 },
      first_seen: T('2026-10-04T02:30:00Z'),
      last_seen: T('2026-10-04T03:05:00Z'),
    },
    banned: true,
  },
  '203.0.113.17': {
    ip: '203.0.113.17',
    first_seen: T('2026-09-30T00:00:00Z'),
    last_seen: T('2026-10-09T03:30:00Z'),
    sessions: 90,
    accounts: [{ name: 'OpAdmin', sessions: 90 }],
    frps: {
      connections: 90,
      by_proxy: { minecraft: 90, ssh: 0 },
      first_seen: T('2026-09-30T00:00:00Z'),
      last_seen: T('2026-10-09T03:30:00Z'),
    },
    banned: false,
  },
};

function fallbackIpInfo(ip: string): IpInfo {
  return {
    ip,
    first_seen: T('2026-10-01T00:00:00Z'),
    last_seen: T('2026-10-09T00:00:00Z'),
    sessions: 1,
    accounts: [{ name: 'OpAdmin', sessions: 1 }],
    frps: { connections: 1, by_proxy: { minecraft: 1 }, first_seen: null, last_seen: null },
    banned: false,
  };
}

const mockAlerts: Alert[] = [
  {
    id: 'al-1', first_ts: T('2026-10-04T02:41:00Z'), last_ts: T('2026-10-04T02:47:00Z'),
    rule: 'anticheat_cluster', severity: 'critical',
    title: 'Scout42 外挂特征集中出现', detail: 'moved too quickly / flying is not enabled，6 分钟内 4 次。',
    subject: 'Scout42', data: { kind: 'moved_too_quickly', count: 4, ip: '203.0.113.96' },
    status: 'open', acked_at: null, count: 4,
  },
  {
    id: 'al-2', first_ts: T('2026-10-04T02:30:00Z'), last_ts: T('2026-10-04T03:05:00Z'),
    rule: 'frps_ssh_spike', severity: 'critical',
    title: '单 IP SSH 代理连接激增', detail: '203.0.113.96 在 35 分钟内发起 46 次 frps ssh 连接，疑似爆破。',
    subject: '203.0.113.96', data: { proxy: 'ssh', connections: 46, ip: '203.0.113.96' },
    status: 'open', acked_at: null, count: 46,
  },
  {
    id: 'al-3', first_ts: T('2026-10-09T01:12:00Z'), last_ts: T('2026-10-09T01:12:00Z'),
    rule: 'unregistered_whitelisted', severity: 'warning',
    title: '白名单账号未在 EasyAuth 注册', detail: 'Zoe、EveX 在白名单内但未注册，可被任何人抢注冒用。',
    subject: 'Zoe', data: { names: ['Zoe', 'EveX'] },
    status: 'open', acked_at: null, count: 2,
  },
  {
    id: 'al-4', first_ts: T('2026-10-04T02:41:00Z'), last_ts: T('2026-10-04T02:41:00Z'),
    rule: 'night_login', severity: 'warning',
    title: '凌晨时段登录', detail: 'Scout42 于 02:41 登录（规则时段 01:00–06:00）。',
    subject: 'Scout42', data: { hour: 2 },
    status: 'open', acked_at: null, count: 1,
  },
  {
    id: 'al-5', first_ts: T('2026-10-04T02:41:00Z'), last_ts: T('2026-10-04T02:41:00Z'),
    rule: 'new_account', severity: 'info',
    title: '新账号加入', detail: 'Scout42 首次出现（首见 2026-10-04 02:41）。',
    subject: 'Scout42', data: {},
    status: 'open', acked_at: null, count: 1,
  },
  {
    id: 'al-6', first_ts: T('2026-10-02T19:22:00Z'), last_ts: T('2026-10-02T19:22:00Z'),
    rule: 'new_ip', severity: 'warning',
    title: '已知账号出现新 IP', detail: 'Yuki_Y 从新 ASN 登录，疑似冒用。',
    subject: 'Yuki_Y', data: { ip: '198.51.100.200' },
    status: 'acked', acked_at: T('2026-10-02T20:00:00Z'), count: 1,
  },
];

const mockRules: RulesResponse['items'] = [
  { id: 'night_login', name: '凌晨时段登录', description: '01:00–06:00 登录触发告警', severity: 'warning', enabled: true },
  { id: 'new_ip', name: '已知账号新 IP/ASN/国家', description: '账号从未见过的来源登录', severity: 'warning', enabled: true },
  { id: 'new_account', name: '新账号注册', description: '首次出现的账号', severity: 'info', enabled: true },
  { id: 'brute_force', name: '单 IP 多账号/多次失败', description: '密码爆破特征，告警并自动 ban-ip', severity: 'critical', enabled: true },
  { id: 'anticheat_cluster', name: '外挂特征集中', description: 'moved too quickly / 飞行被踢 集中出现', severity: 'critical', enabled: true },
  { id: 'fingerprint_change', name: '客户端指纹突变', description: 'carpet 握手突然消失等', severity: 'warning', enabled: true },
  { id: 'frps_ssh_spike', name: 'frps ssh 连接激增', description: '单 IP ssh 代理连接激增', severity: 'critical', enabled: true },
  { id: 'unregistered_whitelisted', name: '白名单未注册', description: '白名单内但 EasyAuth 未注册', severity: 'warning', enabled: true },
];

function computeOpenCounts(): OpenCounts {
  const c: OpenCounts = { critical: 0, warning: 0, info: 0 };
  for (const a of mockAlerts) {
    if (a.status === 'open') c[a.severity] += 1;
  }
  return c;
}

/** Mock-only: register a listener so the fake socket can push live alerts. */
type AlertListener = (a: Alert) => void;
const alertListeners = new Set<AlertListener>();
export function mockOnAlert(fn: AlertListener): () => void {
  alertListeners.add(fn);
  return () => alertListeners.delete(fn);
}
let mockAlertSeq = 100;
export function mockPushAlert(partial?: Partial<Alert>): Alert {
  mockAlertSeq += 1;
  const alert: Alert = {
    id: `al-live-${mockAlertSeq}`,
    first_ts: now(),
    last_ts: now(),
    rule: 'brute_force',
    severity: 'critical',
    title: '检测到密码爆破',
    detail: '198.51.100.77 在 1 分钟内 8 次 EasyAuth 失败，已自动 ban-ip。',
    subject: '198.51.100.77',
    data: { ip: '198.51.100.77', failures: 8 },
    status: 'open',
    acked_at: null,
    count: 8,
    ...partial,
  };
  mockAlerts.unshift(alert);
  for (const fn of alertListeners) fn(alert);
  return alert;
}

// --- M2 mock dataset: QQ bot / whitelist -------------------------------------
// 机器人未启用（旧 bot 仍在运行）；openid 一律掩码，玩家名虚构。

const mockBot = {
  enabled: false,
  robot_name: 'MC助手',
  last_error: null as string | null,
  pushes_today: 7,
  maintenance: { active: false, until: null as string | null },
  monitor: { last_online: true as boolean | null, fail_count: 0 },
  groups: [
    { openid_masked: 'E804...5786', added_at: T('2026-08-02T10:00:00Z'), alert_group: true },
    { openid_masked: 'C19A...0D33', added_at: T('2026-09-11T14:30:00Z'), alert_group: false },
  ],
};

const mockGrants: GrantsResponse['items'] = [
  {
    name: 'NewPlayer01',
    openid_masked: 'A1B2…C3D4',
    granted_at: new Date(Date.now() - 6 * 3600_000).toISOString(),
    deadline: new Date(Date.now() + 42 * 3600_000).toISOString(),
    status: 'pending',
    resolved_at: null,
  },
  {
    name: 'Steve_2026',
    openid_masked: 'E5F6…7788',
    granted_at: new Date(Date.now() - 30 * 3600_000).toISOString(),
    deadline: new Date(Date.now() + 18 * 3600_000).toISOString(),
    status: 'activated',
    resolved_at: new Date(Date.now() - 29 * 3600_000).toISOString(),
  },
];

const mockApplications: Application[] = [
  { name: 'XiaoLei_07', openid_masked: 'F702...11AC', ts: T('2026-10-09T01:20:00Z') },
  { name: 'ChenYu', openid_masked: 'A1B2...9F0E', ts: T('2026-10-08T22:05:00Z') },
];

let interactionSeq = 500;
const mockInteractions: Interaction[] = [
  {
    id: 1, ts: T('2026-10-09T02:40:00Z'), group_masked: 'E804...5786', openid_masked: 'A1B2...9F0E',
    admin: false, cmd: 'status', input: '状态',
    output: '✅ 服务器在线 | 版本 1.21.11 | 在线 2/20：OpAdmin, LiuChen | 已运行 3 小时',
  },
  {
    id: 2, ts: T('2026-10-09T02:35:00Z'), group_masked: 'E804...5786', openid_masked: 'F702...11AC',
    admin: false, cmd: 'join', input: '怎么加入',
    output: '发送「加入 你的游戏ID」即可申请白名单，游戏ID 区分大小写，需与进服时完全一致。',
  },
  {
    id: 3, ts: T('2026-10-09T01:10:00Z'), group_masked: 'E804...5786', openid_masked: 'E804...5786',
    admin: true, cmd: 'approve', input: '批准 XiaoLei_07',
    output: '⚠️ 已把 XiaoLei_07 写入白名单并离线计算 UUID，但重载确认超时，请稍后用「待审」复查。',
  },
  {
    id: 4, ts: T('2026-10-08T23:50:00Z'), group_masked: 'C19A...0D33', openid_masked: 'C19A...0D33',
    admin: false, cmd: 'mods', input: 'mod',
    output: '当前模组：carpet, lithium, ferrite-core, proxy-protocol-support。客户端可选装 carpet 以显示 TPS。',
  },
  {
    id: 5, ts: T('2026-10-08T20:12:00Z'), group_masked: 'E804...5786', openid_masked: 'D0E1...77B9',
    admin: false, cmd: 'unknown', input: '服务器几点开',
    output: '❓ 没听懂这条指令，发送「帮助」查看可用指令。',
  },
];

const mockWhitelist: WhitelistEntry[] = [
  {
    name: 'OpAdmin', uuid: '7f8e9a10-1b2c-3d4e-5f60-718293a4b5c6',
    easyauth_registered: true, members: ['E804...5786'],
  },
  {
    name: 'LiuChen', uuid: 'a1b2c3d4-e5f6-7081-92a3-b4c5d6e7f809',
    easyauth_registered: true, members: ['C19A...0D33'],
  },
  {
    name: 'Zoe', uuid: 'c0ffee12-3456-7890-abcd-ef0123456789',
    easyauth_registered: false, members: ['E804...5786'],
  },
  {
    name: 'EveX', uuid: 'dead10cc-aaaa-bbbb-cccc-ddddeeeeffff',
    easyauth_registered: false, members: [],
  },
];

// --- M3 mock dataset: 运维任务 / 备份 -----------------------------------------

const mockOps = {
  running: null as string | null,
  watchdog: { suspended: false, recent_crashes: 0 },
};

/** 下次 05:00（本地时区）的 ISO 时间。 */
function nextRestartIso(): string {
  const d = new Date();
  d.setHours(5, 0, 0, 0);
  if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1);
  return d.toISOString();
}

const mockJobRuns: JobRun[] = [
  {
    id: 42,
    job: 'scheduled_restart',
    trigger: 'schedule',
    started_at: T('2026-10-09T05:00:03Z'),
    finished_at: T('2026-10-09T05:02:11Z'),
    status: 'ok',
    log: '05:00:03 开始 5 分钟预警\n05:00:03 关服\n05:00:41 冷备份 mc-backup-20261009-050041.tar.gz\n05:02:08 清理旧备份：保留 7 份\n05:02:11 开服完成',
    result: { backup: 'mc-backup-20261009-050041.tar.gz', size: 486539264 },
  },
  {
    id: 41,
    job: 'backup',
    trigger: 'web:admin',
    started_at: T('2026-10-08T21:14:02Z'),
    finished_at: T('2026-10-08T21:14:37Z'),
    status: 'ok',
    log: '21:14:02 开始不停服备份\n21:14:37 完成 mc-backup-20261008-211402.tar.gz',
    result: { backup: 'mc-backup-20261008-211402.tar.gz', size: 471859200 },
  },
  {
    id: 40,
    job: 'scheduled_restart',
    trigger: 'schedule',
    started_at: T('2026-10-08T05:00:02Z'),
    finished_at: T('2026-10-08T05:00:19Z'),
    status: 'failed',
    log: '05:00:02 开始 5 分钟预警\n05:00:19 失败：BackupError: 备份文件为空',
    result: {},
  },
];

const mockBackups: Backup[] = [
  { name: 'mc-backup-20261009-050041.tar.gz', size: 486539264, mtime: T('2026-10-09T05:02:08Z') },
  { name: 'mc-backup-20261008-211402.tar.gz', size: 471859200, mtime: T('2026-10-08T21:14:37Z') },
  { name: 'mc-backup-20261008-050033.tar.gz', size: 465567744, mtime: T('2026-10-08T05:01:55Z') },
  { name: 'mc-backup-20261007-050029.tar.gz', size: 1288490188, mtime: T('2026-10-07T05:01:40Z') },
];

/** 极简机器人指令模拟器：把一条消息映射到 cmd + 风格化 reply。 */
function simulateBotReply(content: string, asAdmin: boolean): SimulateResponse {
  const text = content.trim();
  const online = state.server.players?.names ?? [];
  if (/^状态$|^status$/i.test(text)) {
    return {
      cmd: 'status',
      reply: `✅ 服务器在线 | 版本 ${state.server.mc_version} | 在线 ${online.length}/20：${online.join(', ') || '无'}`,
    };
  }
  if (/在线|玩家|who/i.test(text)) {
    return { cmd: 'players', reply: online.length ? `👥 当前在线：${online.join(', ')}` : '当前无人在线。' };
  }
  if (/^mod/i.test(text) || /模组/.test(text)) {
    return { cmd: 'mods', reply: '📦 当前模组：carpet, lithium, ferrite-core, proxy-protocol-support。' };
  }
  if (/怎么加入|加入|白名单/.test(text)) {
    return {
      cmd: 'join',
      reply: '发送「加入 你的游戏ID」申请白名单。游戏ID 区分大小写，须与进服时完全一致。',
    };
  }
  if (/^帮助$|^help$|指令/i.test(text)) {
    return {
      cmd: 'help',
      reply: '可用指令：状态 / 在线玩家 / mod / 怎么加入 / 帮助' + (asAdmin ? ' / 待审 / 批准 <ID> / 驳回 <ID>' : ''),
    };
  }
  if (/待审|申请/.test(text)) {
    if (!asAdmin) return { cmd: 'pending', reply: '❌ 仅管理员可查看待审申请。' };
    const names = mockApplications.map((a) => a.name).join('、') || '无';
    return { cmd: 'pending', reply: `📋 待审申请（${mockApplications.length}）：${names}` };
  }
  const approve = text.match(/^批准\s+(\S+)/);
  if (approve) {
    if (!asAdmin) return { cmd: 'approve', reply: '❌ 仅管理员可批准申请。' };
    return { cmd: 'approve', reply: `✅ 已把 ${approve[1]} 加入白名单（离线计算 UUID，已重载并回读确认）。` };
  }
  return { cmd: 'unknown', reply: '❓ 没听懂这条指令，发送「帮助」查看可用指令。' };
}

// --- M4 mock dataset: 通知设置 -----------------------------------------------
// 机器人未启用；openid 掩码；邮件默认关闭，无密码。

const mockNotify: NotifySettings = {
  email: {
    enabled: false,
    host: 'smtp.qq.com',
    port: 465,
    security: 'ssl',
    username: '',
    sender: 'mc-ops@example.com',
    to: ['admin@example.com'],
    severities: ['critical', 'warning'],
    max_per_hour: 20,
    has_password: false,
  },
  qq: {
    enabled: false,
    severities: ['critical'],
    groups: [
      { openid: 'E8041F2A9C7B5786', label: '管理群' },
    ],
  },
  rules: [
    {
      id: 'night_login',
      name: '凌晨时段登录',
      description: '01:00–06:00 登录触发告警',
      default_severity: 'warning',
      enabled: true,
      severity: null,
    },
    {
      id: 'anticheat_kick',
      name: '外挂/飞行被踢',
      description: 'moved too quickly / flying is not enabled 集中出现',
      default_severity: 'critical',
      enabled: true,
      severity: null,
    },
    {
      id: 'flight_suspect',
      name: '疑似飞行',
      description: '允许飞行后仍异常，仅告警不自动踢',
      default_severity: 'warning',
      enabled: true,
      severity: 'info',
    },
    {
      id: 'server_crash_loop',
      name: '崩溃重启循环',
      description: '短时间内多次崩溃，看门狗熔断',
      default_severity: 'critical',
      enabled: true,
      severity: null,
    },
    {
      id: 'unregistered_whitelisted',
      name: '白名单未注册',
      description: '白名单内但 EasyAuth 未注册，可被抢注',
      default_severity: 'warning',
      enabled: true,
      severity: null,
    },
  ],
  known_groups: [
    {
      openid: 'E8041F2A9C7B5786',
      openid_masked: 'E804...5786',
      added_at: T('2026-08-02T10:00:00Z'),
      is_alert: true,
    },
    {
      openid: 'C19A4D5E6F700D33',
      openid_masked: 'C19A...0D33',
      added_at: T('2026-09-11T14:30:00Z'),
      is_alert: false,
    },
    {
      openid: 'A1B2C3D4E5F69F0E',
      openid_masked: 'A1B2...9F0E',
      added_at: T('2026-10-01T08:00:00Z'),
      is_alert: false,
    },
  ],
  bot: { enabled: false, connected: false },
  stats: {
    email_sent_hour: 3,
    email_dropped: 1,
    qq_sent_today: 7,
    last_error: {
      email: null,
      qq: '机器人未启用：告警未推送到 QQ',
    },
  },
};

export const mockApi = {
  async login(password: string): Promise<MeResponse> {
    await delay();
    if (password !== MOCK_PASSWORD) {
      throw new ApiError(401, '密码错误');
    }
    state.loggedIn = true;
    return { user: 'admin' };
  },
  async logout(): Promise<void> {
    await delay();
    state.loggedIn = false;
  },
  async me(): Promise<MeResponse> {
    await delay(50);
    if (!state.loggedIn) throw new ApiError(401, 'unauthorized');
    return { user: 'admin' };
  },
  async health(): Promise<HealthResponse> {
    await delay(50);
    return { status: 'ok', version: '0.0.0-mock' };
  },
  async system(): Promise<SystemResponse> {
    await delay(80);
    return {
      // M3 已上线：面板接管控制（启停/重启、定时任务、备份均可用）。
      mode: 'control',
      modules: {
        server: { state: 'running', restarts: 0 },
        logs: { state: 'running', restarts: 0 },
        scheduler: { state: 'running', restarts: 0 },
        bot: { state: 'stopped', detail: 'M2 未上线', restarts: 0 },
        qq_bot: { state: 'stopped', detail: '未启用：旧机器人仍在运行', restarts: 0 },
        security: { state: 'running', restarts: 0 },
        edge: { state: 'running', restarts: 1 },
      },
    };
  },
  async serverStatus(): Promise<ServerStatus> {
    await delay(60);
    return mockServerStatus();
  },
  async serverStart(): Promise<AcceptedResponse> {
    await delay();
    mockSetServerState('starting');
    setTimeout(() => mockSetServerState('running'), 1500);
    return { accepted: true };
  },
  async serverStop(): Promise<AcceptedResponse> {
    await delay();
    mockSetServerState('stopping');
    setTimeout(() => mockSetServerState('stopped'), 1500);
    return { accepted: true };
  },
  async serverRestart(): Promise<AcceptedResponse> {
    await delay();
    mockSetServerState('starting');
    setTimeout(() => mockSetServerState('running'), 2000);
    return { accepted: true };
  },
  async sendCommand(command: string): Promise<CommandResponse> {
    await delay(120);
    return { output: `[mock] 执行命令: ${command}` };
  },
  async events(params?: { limit?: number; types?: string[] }): Promise<EventsResponse> {
    await delay(80);
    let items = [...events].reverse();
    if (params?.types && params.types.length > 0) {
      const set = new Set(params.types);
      items = items.filter((e) => set.has(e.type));
    }
    if (params?.limit !== undefined) items = items.slice(0, params.limit);
    return { items };
  },

  // --- M1 ---
  async players(params?: { q?: string; limit?: number }): Promise<PlayersResponse> {
    await delay(90);
    let items = [...mockPlayers];
    if (params?.q) {
      const q = params.q.toLowerCase();
      items = items.filter((p) => p.name.toLowerCase().includes(q));
    }
    if (params?.limit !== undefined) items = items.slice(0, params.limit);
    return { items };
  },

  async player(name: string): Promise<PlayerDetailResponse> {
    await delay(90);
    const player = mockPlayers.find((p) => p.name === name);
    if (!player) throw new ApiError(404, '玩家不存在');
    const sessions = mockSessionsByPlayer[name] ?? [];
    const ips =
      mockIpsByPlayer[name] ??
      (player.last_ip
        ? [{ ip: player.last_ip, sessions: player.sessions, first_seen: player.first_seen, last_seen: player.last_seen, trusted: player.last_ip_trusted }]
        : []);
    const events = events_for(name);
    return { player, sessions, ips, events };
  },

  async sessions(params?: { limit?: number; online?: boolean }): Promise<SessionsResponse> {
    await delay(80);
    let items = Object.values(mockSessionsByPlayer).flat();
    items.sort((a, b) => new Date(b.login_at).getTime() - new Date(a.login_at).getTime());
    if (params?.online === true) items = items.filter((s) => s.logout_at === null);
    if (params?.online === false) items = items.filter((s) => s.logout_at !== null);
    if (params?.limit !== undefined) items = items.slice(0, params.limit);
    return { items };
  },

  async ip(ip: string): Promise<IpInfo> {
    await delay(90);
    return mockIpInfo[ip] ?? fallbackIpInfo(ip);
  },

  async alerts(params?: { status?: AlertStatus | 'all'; limit?: number }): Promise<AlertsResponse> {
    await delay(90);
    const status = params?.status ?? 'open';
    let items = [...mockAlerts];
    if (status !== 'all') items = items.filter((a) => a.status === status);
    items.sort((a, b) => new Date(b.last_ts).getTime() - new Date(a.last_ts).getTime());
    if (params?.limit !== undefined) items = items.slice(0, params.limit);
    return { items, open_counts: computeOpenCounts() };
  },

  async ackAlert(id: string): Promise<{ ok: true }> {
    await delay(100);
    const a = mockAlerts.find((x) => x.id === id);
    if (a) {
      a.status = 'acked';
      a.acked_at = now();
    }
    return { ok: true };
  },

  async ackAllAlerts(severity?: string): Promise<AckAllResponse> {
    await delay(120);
    let acked = 0;
    for (const a of mockAlerts) {
      if (a.status === 'open' && (!severity || a.severity === severity)) {
        a.status = 'acked';
        a.acked_at = now();
        acked += 1;
      }
    }
    return { acked };
  },

  async securityRules(): Promise<RulesResponse> {
    await delay(70);
    return { items: mockRules.map((r) => ({ ...r })) };
  },

  async edgeStatus(): Promise<EdgeStatus> {
    await delay(70);
    return {
      enabled: true,
      connected: true,
      last_event_ts: now(),
      error: null,
      events_24h: 1832,
    };
  },

  // --- M2: QQ bot ---
  async botStatus(): Promise<BotStatus> {
    await delay(80);
    return {
      enabled: mockBot.enabled,
      // 未启用时无连接；启用后以 monitor 的 last_online 作为连接状态。
      connected: mockBot.enabled ? mockBot.monitor.last_online === true : false,
      robot_name: mockBot.robot_name,
      last_error: mockBot.last_error,
      groups: mockBot.groups.map((g) => ({ ...g })),
      pushes_today: mockBot.pushes_today,
      maintenance: { ...mockBot.maintenance },
      monitor: { ...mockBot.monitor },
    };
  },

  async botGrants(): Promise<GrantsResponse> {
    await delay(60);
    return { activation_hours: 48, items: mockGrants.map((g) => ({ ...g })) };
  },

  async botApplications(): Promise<ApplicationsResponse> {
    await delay(80);
    return { items: mockApplications.map((a) => ({ ...a })) };
  },

  async botApproveApplication(name: string): Promise<BotReplyResponse> {
    await delay(140);
    const idx = mockApplications.findIndex((a) => a.name === name);
    if (idx === -1) throw new ApiError(409, `申请不存在或已处理：${name}`);
    const [app] = mockApplications.splice(idx, 1);
    if (app && !mockWhitelist.some((w) => w.name === app.name)) {
      mockWhitelist.push({
        name: app.name,
        uuid: '00000000-0000-0000-0000-000000000000',
        easyauth_registered: false,
        members: [app.openid_masked],
      });
    }
    return { reply: `✅ 已批准 ${name}：离线计算 UUID 写入白名单，已重载并回读确认。` };
  },

  async botRejectApplication(name: string): Promise<BotReplyResponse> {
    await delay(120);
    const idx = mockApplications.findIndex((a) => a.name === name);
    if (idx === -1) throw new ApiError(409, `申请不存在或已处理：${name}`);
    mockApplications.splice(idx, 1);
    return { reply: `✅ 已驳回 ${name} 的申请。` };
  },

  async botInteractions(params?: { limit?: number; q?: string }): Promise<InteractionsResponse> {
    await delay(90);
    let items = [...mockInteractions].sort(
      (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
    );
    if (params?.q) {
      const q = params.q.toLowerCase();
      items = items.filter(
        (it) =>
          it.input.toLowerCase().includes(q) ||
          it.output.toLowerCase().includes(q) ||
          it.cmd.toLowerCase().includes(q),
      );
    }
    if (params?.limit !== undefined) items = items.slice(0, params.limit);
    return { items };
  },

  async botSimulate(content: string, asAdmin: boolean): Promise<SimulateResponse> {
    await delay(160);
    const res = simulateBotReply(content, asAdmin);
    // 记录到交互历史（标注模拟），让交互记录页也能看到。
    interactionSeq += 1;
    mockInteractions.push({
      id: interactionSeq,
      ts: now(),
      group_masked: '模拟器',
      openid_masked: asAdmin ? 'ADMIN...SIM' : 'USER...SIM',
      admin: asAdmin,
      cmd: res.cmd,
      input: content,
      output: res.reply,
    });
    return res;
  },

  // --- M2: whitelist ---
  async whitelist(): Promise<WhitelistResponse> {
    await delay(90);
    return { items: mockWhitelist.map((w) => ({ ...w, members: [...w.members] })) };
  },

  async whitelistAdd(name: string): Promise<BotReplyResponse> {
    await delay(160);
    if (!/^[A-Za-z0-9_]{3,16}$/.test(name)) {
      throw new ApiError(400, '游戏 ID 不合法：仅限 3–16 位字母、数字或下划线');
    }
    if (mockWhitelist.some((w) => w.name === name)) {
      throw new ApiError(409, `${name} 已在白名单中`);
    }
    mockWhitelist.push({
      name,
      uuid: 'feedface-0000-0000-0000-000000000000',
      easyauth_registered: false,
      members: [],
    });
    return { reply: `✅ 已添加 ${name}：按原始大小写离线计算 UUID 写入，已重载并回读确认。` };
  },

  async whitelistRemove(name: string): Promise<BotReplyResponse> {
    await delay(140);
    const idx = mockWhitelist.findIndex((w) => w.name === name);
    if (idx === -1) throw new ApiError(409, `${name} 不在白名单中`);
    mockWhitelist.splice(idx, 1);
    return { reply: `✅ 已移除 ${name}，该玩家将无法进服。` };
  },

  // --- M3: 运维任务 / 备份 ---
  async jobs(): Promise<JobsResponse> {
    await delay(90);
    const jobs: Job[] = [
      {
        id: 'scheduled_restart',
        name: '定时重启 + 冷备份',
        schedule: '05:00',
        next_run: nextRestartIso(),
      },
      { id: 'backup', name: '立即备份（不停服）', schedule: null, next_run: null },
    ];
    return {
      jobs,
      running: mockOps.running,
      watchdog: { ...mockOps.watchdog },
      history: mockJobRuns.map((r) => ({ ...r, result: { ...r.result } })),
    };
  },

  async runJob(job: string): Promise<JobRunAccepted> {
    await delay(140);
    if (job !== 'scheduled_restart' && job !== 'backup') {
      throw new ApiError(404, '未知任务');
    }
    if (mockOps.running) throw new ApiError(409, `任务 ${mockOps.running} 正在运行`);
    mockOps.running = job;
    const now2 = now();
    const id = (mockJobRuns[0]?.id ?? 0) + 1;
    mockJobRuns.unshift({
      id,
      job,
      trigger: 'web:admin',
      started_at: now2,
      finished_at: null,
      status: 'running',
      log: `${new Date().toTimeString().slice(0, 8)} 已开始`,
      result: {},
    });
    // 模拟任务在 2 秒后完成。
    setTimeout(() => {
      mockOps.running = null;
      const run = mockJobRuns.find((r) => r.id === id);
      if (run) {
        run.finished_at = now();
        run.status = 'ok';
        if (job === 'backup') {
          const name = `mc-backup-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}-mock.tar.gz`;
          run.log += `\n完成 ${name}`;
          run.result = { backup: name, size: 470000000 };
          mockBackups.unshift({ name, size: 470000000, mtime: now() });
        } else {
          run.log += '\n开服完成';
        }
      }
    }, 2000);
    return { accepted: true };
  },

  async watchdogReset(): Promise<WatchdogResetResponse> {
    await delay(100);
    mockOps.watchdog = { suspended: false, recent_crashes: 0 };
    return { ok: true };
  },

  async backups(): Promise<BackupsResponse> {
    await delay(90);
    return { items: mockBackups.map((b) => ({ ...b })) };
  },

  async restic(repo: ResticRepoId = 'local'): Promise<ResticInfo> {
    await delay(90);
    const now = Date.now();
    return {
      enabled: true,
      repo,
      repos: [
        { id: 'local', name: '本机' },
        { id: 'nas', name: '局域网 NAS（SMB）' },
      ],
      restore_running: null,
      snapshots: Array.from({ length: 5 }, (_, i) => ({
        id: (0x3fa2c1d0 + i * 7919).toString(16).slice(0, 8),
        time: new Date(now - i * 3600_000).toISOString(),
        tags: ['hourly'],
        total_bytes: 4.6 * 2 ** 30,
        added_bytes: (i === 4 ? 4.1 * 2 ** 30 : 38 * 2 ** 20),
      })),
      stats: repo === 'local' ? { repo_bytes: 4.3 * 2 ** 30, uncompressed_bytes: 4.9 * 2 ** 30 } : null,
    };
  },

  async restoreSnapshot(): Promise<JobRunAccepted> {
    await delay(120);
    return { accepted: true };
  },

  // --- M4: 通知设置 ---
  async settingsNotify(): Promise<NotifySettings> {
    await delay(90);
    return structuredClone(mockNotify);
  },

  async updateSettingsNotify(body: NotifySettingsUpdate): Promise<NotifySettings> {
    await delay(140);
    if (body.email.enabled && !body.email.host.trim()) {
      throw new ApiError(422, '启用邮件时必须填写 SMTP 主机');
    }
    if (body.email.enabled && body.email.to.length === 0) {
      throw new ApiError(422, '启用邮件时至少需要一个收件人');
    }
    // email：写回除 password 外的字段；password 决定 has_password。
    mockNotify.email = {
      ...mockNotify.email,
      enabled: body.email.enabled,
      host: body.email.host,
      port: body.email.port,
      security: body.email.security,
      username: body.email.username,
      sender: body.email.sender,
      to: [...body.email.to],
      severities: [...body.email.severities],
      max_per_hour: body.email.max_per_hour,
    };
    if (body.email.password !== undefined && body.email.password !== null) {
      mockNotify.email.has_password = body.email.password !== '';
    }
    // qq：写回启用/级别；groups 按勾选同步 is_alert + label。
    const selected = new Map(body.qq.groups.map((g) => [g.openid, g.label]));
    mockNotify.qq = {
      enabled: body.qq.enabled,
      severities: [...body.qq.severities],
      groups: body.qq.groups.map((g) => ({ ...g })),
    };
    for (const kg of mockNotify.known_groups) {
      kg.is_alert = selected.has(kg.openid);
    }
    // rules：按 id 应用覆盖。
    for (const r of mockNotify.rules) {
      const ov = body.rules[r.id];
      if (ov) {
        r.enabled = ov.enabled;
        r.severity = ov.severity;
      }
    }
    mockNotify.bot = { enabled: body.qq.enabled, connected: body.qq.enabled };
    mockNotify.stats.last_error.qq =
      body.qq.enabled ? null : '机器人未启用：告警未推送到 QQ';
    return structuredClone(mockNotify);
  },

  async testNotify(channel: NotifyChannel): Promise<NotifyTestResult> {
    await delay(400);
    if (channel === 'email') {
      if (!mockNotify.email.enabled) {
        return { ok: false, detail: '邮件渠道未启用。' };
      }
      if (!mockNotify.email.has_password) {
        return { ok: false, detail: '尚未设置授权码/密码，无法发送。' };
      }
      return { ok: true, detail: `已向 ${mockNotify.email.to.join('、')} 发送测试邮件。` };
    }
    if (!mockNotify.qq.enabled || !mockNotify.bot.connected) {
      return { ok: false, detail: '机器人未启用或未连接。' };
    }
    const n = mockNotify.known_groups.filter((g) => g.is_alert).length;
    return { ok: true, detail: `已向 ${n} 个告警群发送测试消息。` };
  },

  // --- P1: 配置 / server.properties / 账号与机密 / capabilities ---
  async config(): Promise<ConfigResponse> {
    await delay(90);
    return mockConfig();
  },
  async updateConfig(values: ConfigValues, note?: string): Promise<ConfigUpdateResult> {
    await delay(140);
    return mockUpdateConfig(values, note);
  },
  async resetConfig(paths: string[]): Promise<ConfigUpdateResult> {
    await delay(120);
    return mockResetConfig(paths);
  },
  async configHistory(limit = 50): Promise<ConfigHistoryResponse> {
    await delay(90);
    return mockHistory(limit);
  },
  async restoreConfigHistory(id: number): Promise<ConfigUpdateResult> {
    await delay(140);
    return mockRestoreHistory(id);
  },
  async restartAgent(): Promise<RestartAgentResult> {
    await delay(200);
    return mockRestartAgent();
  },
  async capabilities(): Promise<Capabilities> {
    await delay(60);
    return mockCapabilities();
  },
  async properties(): Promise<PropertiesResponse> {
    await delay(90);
    return mockProperties();
  },
  async updateProperties(
    changes: Record<string, string | number | boolean>,
  ): Promise<PropertiesUpdateResult> {
    await delay(140);
    return mockUpdateProperties(changes);
  },
  async changePassword(oldPassword: string, newPassword: string): Promise<void> {
    await delay(160);
    mockChangePassword(oldPassword, newPassword);
  },
  async totpStatus(): Promise<TotpStatus> {
    await delay(70);
    return mockTotpStatus();
  },
  async totpSetup(): Promise<TotpSetupResponse> {
    await delay(120);
    return mockTotpSetup();
  },
  async totpConfirm(code: string): Promise<TotpStatus> {
    await delay(140);
    return mockTotpConfirm(code);
  },
  async totpDisable(password: string): Promise<TotpStatus> {
    await delay(140);
    return mockTotpDisable(password);
  },
  async secrets(): Promise<SecretsResponse> {
    await delay(80);
    return mockSecrets();
  },
  async updateSecrets(values: Record<string, string>): Promise<SecretsUpdateResult> {
    await delay(140);
    return mockUpdateSecrets(values);
  },
};

function events_for(name: string): AppEvent[] {
  return events.filter((e) => e.data['name'] === name).slice(-10).reverse();
}
