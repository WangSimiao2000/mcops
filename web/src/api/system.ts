// P4：主机资源、自检诊断、首次安装向导、mc-ops 自身升级。后端见 hoststats.py / diagnostics.py / api_system.py。

import { apiFetch } from './http';

export interface DiskInfo {
  labels: string[];
  path: string;
  total: number;
  used: number;
  free: number;
}

export interface HostSnapshot {
  ts: number;
  cpu: { pct: number | null; cores: number; load: number[] };
  mem: { total: number; available: number; used: number; swap_total: number; swap_used: number };
  disks: DiskInfo[];
  net: { rx_bps: number; tx_bps: number } | null;
  mc: { pid: number; rss: number; cpu_pct: number | null } | null;
  uptime_s: number | null;
}

export interface HostPoint {
  ts: number;
  cpu: number | null;
  mem: number | null;
  mc_rss: number | null;
  mc_cpu: number | null;
  rx: number | null;
  tx: number | null;
}

export interface HostStats {
  current: HostSnapshot | null;
  history: HostPoint[];
  interval_s: number;
}

export type CheckStatus = 'ok' | 'warn' | 'fail' | 'info';

export interface DiagCheck {
  id: string;
  group: string;
  title: string;
  status: CheckStatus;
  detail: string;
  fix: string;
}

export interface SetupStatus {
  needs_setup: boolean;
  game_dir: string;
  dir_empty: boolean;
  memory_total_gb: number;
  running_job: string | null;
}

export interface SetupRequest {
  mc: string;
  loader: string;
  accept_eula: boolean;
  memory_gb: number;
  motd: string;
  max_players: number;
  online_mode: boolean;
  white_list: boolean;
  difficulty: 'peaceful' | 'easy' | 'normal' | 'hard';
  view_distance: number;
  port: number;
  java_mirror: 'tuna' | 'official';
}

export interface UpdatesInfo {
  current: string | null;
  bundles: { revision: string; current: boolean; installed_at: string }[];
}

export interface SystemApi {
  host(signal?: AbortSignal): Promise<HostStats>;
  diagnostics(): Promise<{ items: DiagCheck[] }>;
  setupStatus(): Promise<SetupStatus>;
  setupInstall(req: SetupRequest): Promise<{ accepted: boolean }>;
  updates(): Promise<UpdatesInfo>;
  uploadBundle(file: File): Promise<{ revision: string; current: string | null }>;
  installRevision(revision: string): Promise<{ accepted: boolean }>;
}

const real: SystemApi = {
  host(signal) {
    return apiFetch<HostStats>('/host/stats', signal ? { signal } : {});
  },
  diagnostics() {
    return apiFetch('/system/diagnostics');
  },
  setupStatus() {
    return apiFetch<SetupStatus>('/setup/status');
  },
  setupInstall(req) {
    return apiFetch('/setup/install', { method: 'POST', body: req });
  },
  updates() {
    return apiFetch<UpdatesInfo>('/system/updates');
  },
  uploadBundle(file) {
    return apiFetch('/system/updates/upload', { method: 'POST', rawBody: file });
  },
  installRevision(revision) {
    return apiFetch('/system/updates/install', { method: 'POST', body: { revision } });
  },
};

// ---- mock ----

const GB = 2 ** 30;
const delay = <T>(v: T) => new Promise<T>((r) => setTimeout(() => r(v), 120));

function mockHistory(): HostPoint[] {
  const now = Date.now() / 1000;
  return Array.from({ length: 360 }, (_, i) => {
    const t = now - (359 - i) * 10;
    const wave = Math.sin(i / 18);
    return {
      ts: t,
      cpu: 18 + wave * 8 + (i % 37 === 0 ? 30 : 0),
      mem: 62 + wave * 3,
      mc_rss: 7.2 * GB + wave * 0.3 * GB,
      mc_cpu: 55 + wave * 20,
      rx: 120_000 + wave * 60_000,
      tx: 260_000 + wave * 90_000,
    };
  });
}

const mock: SystemApi = {
  async host() {
    const h = mockHistory();
    return delay({
      interval_s: 10,
      history: h,
      current: {
        ts: Date.now() / 1000,
        cpu: { pct: h[h.length - 1]!.cpu, cores: 4, load: [0.82, 0.95, 1.1] },
        mem: {
          total: 15.6 * GB,
          available: 5.9 * GB,
          used: 9.7 * GB,
          swap_total: 2 * GB,
          swap_used: 0.3 * GB,
        },
        disks: [
          {
            labels: ['游戏目录', '冷备份', '增量快照', '数据库'],
            path: '/home/steve',
            total: 233 * GB,
            used: 48 * GB,
            free: 174 * GB,
          },
        ],
        net: { rx_bps: 118_000, tx_bps: 251_000 },
        mc: { pid: 228953, rss: 7.4 * GB, cpu_pct: 61 },
        uptime_s: 3 * 86400 + 5 * 3600,
      },
    });
  },
  async diagnostics() {
    return delay({
      items: [
        {
          id: 'jar',
          group: '服务端',
          title: '服务端 jar',
          status: 'ok',
          detail: 'fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar',
          fix: '',
        },
        {
          id: 'java',
          group: '服务端',
          title: 'Java',
          status: 'ok',
          detail: 'Java 21（/usr/bin/java）',
          fix: '',
        },
        { id: 'rcon', group: '服务端', title: 'RCON', status: 'ok', detail: '127.0.0.1:25575 可用', fix: '' },
        {
          id: 'disk',
          group: '主机',
          title: '磁盘',
          status: 'ok',
          detail: '剩余 174 GB / 共 233 GB',
          fix: '',
        },
        { id: 'linger', group: '主机', title: '开机自启（linger）', status: 'ok', detail: '已开启', fix: '' },
        { id: 'backup', group: '备份', title: '冷备份', status: 'ok', detail: '最近一次 13 小时前', fix: '' },
        {
          id: 'pw',
          group: '安全',
          title: '面板密码',
          status: 'warn',
          detail: '已改密码，但初始密码文件还在',
          fix: 'rm ~/.config/mcops/admin-password.txt',
        },
        {
          id: 'totp',
          group: '安全',
          title: '两步验证',
          status: 'warn',
          detail: '未开启',
          fix: '设置 → 账号与安全 → 开启两步验证',
        },
        { id: 'bot', group: '组件', title: 'QQ 机器人', status: 'ok', detail: '已连接', fix: '' },
        { id: 'version', group: '组件', title: 'mc-ops 版本', status: 'info', detail: 'ed7dc91', fix: '' },
      ],
    });
  },
  async setupStatus() {
    return delay({
      needs_setup: false,
      game_dir: '/home/steve/minecraft/server',
      dir_empty: false,
      memory_total_gb: 16,
      running_job: null,
    });
  },
  async setupInstall() {
    return delay({ accepted: true });
  },
  async updates() {
    return delay({
      current: 'ed7dc91',
      bundles: [
        { revision: 'ed7dc91', current: true, installed_at: '2026-10-10T09:20:00Z' },
        { revision: '13a0cb4', current: false, installed_at: '2026-10-10T08:14:00Z' },
      ],
    });
  },
  async uploadBundle(file) {
    if (!file.name.endsWith('.tar.gz')) throw new Error('不是有效的 .tar.gz');
    return delay({ revision: 'abc1234', current: 'ed7dc91' });
  },
  async installRevision() {
    return delay({ accepted: true });
  },
};

export const systemApi: SystemApi = import.meta.env.VITE_MOCK === '1' ? mock : real;
