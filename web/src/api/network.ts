import { apiFetch } from './http';

// ---- 类型 ----------------------------------------------------------------

export type NetWindow = '1h' | '24h' | '7d' | '30d';
export type IpTag = 'player' | 'scanner' | 'ssh' | 'banned';

export interface EdgeStatus {
  enabled: boolean;
  connected: boolean;
  last_event_ts: string | null;
  error: string | null;
}

export interface McSelfCheck {
  ok: boolean;
  online?: number;
  max?: number;
  latency_ms?: number;
  version?: string | null;
  error?: string;
}

export interface PortRow {
  port: number;
  proxy: string;
  desc: string;
}

export interface Overview {
  edge: EdgeStatus;
  frpc: { status: string };
  mc: McSelfCheck;
  /** 未配置边缘采集时为 null */
  frps_host: string | null;
  ports: PortRow[];
}

export interface ProxyStat {
  connections: number;
  distinct_ips: number;
}

export interface SeriesPoint {
  bucket: string;
  counts: Record<string, number>;
}

export interface Stats {
  window: NetWindow;
  bucket: 'hour' | 'day';
  by_proxy: Record<string, ProxyStat>;
  proxies: string[];
  series: SeriesPoint[];
  /** 分桶所用时区相对 UTC 的分钟偏移（480 = 北京时间）；旧版后端没有此字段。 */
  tz_offset_min?: number;
}

export interface IpRow {
  ip: string;
  conns: number;
  first_seen: string | null;
  last_seen: string | null;
  proxies: string[];
  banned: boolean;
  tags: IpTag[];
  players: string[];
  player_last_login: string | null;
}

export interface IpList {
  items: IpRow[];
  total: number;
  window: NetWindow;
  limit: number;
  offset: number;
}

export interface RecentConn {
  ts: string | null;
  proxy: string;
  ip: string;
  port: number | null;
  tags: IpTag[];
  players: string[];
}

export interface SshSource {
  ip: string;
  conns: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface BanEntry {
  ip: string;
  reason: string | null;
  created: string | null;
  source: string | null;
  expires: string | null;
}

export interface ScannerRow {
  ip: string;
  conns: number;
  first_seen: string | null;
  last_seen: string | null;
}

export interface ScannerPreview {
  window: NetWindow;
  count: number;
  items: ScannerRow[];
}

export interface BanResult {
  ip: string;
  ok: boolean;
  output?: string;
  error?: string;
}

export interface ScannerBanResult {
  total: number;
  ok: number;
  results: BanResult[];
}

export interface IpListParams {
  window?: NetWindow;
  q?: string;
  tag?: IpTag;
  limit?: number;
  offset?: number;
}

export interface NetworkApi {
  overview(signal?: AbortSignal): Promise<Overview>;
  stats(window: NetWindow, signal?: AbortSignal): Promise<Stats>;
  ips(params: IpListParams, signal?: AbortSignal): Promise<IpList>;
  recent(limit?: number, signal?: AbortSignal): Promise<{ items: RecentConn[] }>;
  ssh(signal?: AbortSignal): Promise<{ hours: number; items: SshSource[] }>;
  bans(signal?: AbortSignal): Promise<{ items: BanEntry[] }>;
  ban(ip: string, reason?: string): Promise<BanResult>;
  pardon(ip: string): Promise<BanResult>;
  scanners(window: NetWindow, signal?: AbortSignal): Promise<ScannerPreview>;
  banScanners(window: NetWindow, ips?: string[]): Promise<ScannerBanResult>;
}

// ---- 真实实现 ------------------------------------------------------------

function ipsQuery(p: IpListParams): string {
  const s = new URLSearchParams();
  if (p.window) s.set('window', p.window);
  if (p.q) s.set('q', p.q);
  if (p.tag) s.set('tag', p.tag);
  if (p.limit !== undefined) s.set('limit', String(p.limit));
  if (p.offset !== undefined) s.set('offset', String(p.offset));
  return s.toString();
}

const realNetworkApi: NetworkApi = {
  overview(signal) {
    return apiFetch<Overview>('/network/overview', signal ? { signal } : {});
  },
  stats(window, signal) {
    return apiFetch<Stats>(`/network/stats?window=${window}`, signal ? { signal } : {});
  },
  ips(params, signal) {
    const q = ipsQuery(params);
    return apiFetch<IpList>(`/network/ips${q ? `?${q}` : ''}`, signal ? { signal } : {});
  },
  recent(limit, signal) {
    const q = limit !== undefined ? `?limit=${limit}` : '';
    return apiFetch<{ items: RecentConn[] }>(`/network/recent${q}`, signal ? { signal } : {});
  },
  ssh(signal) {
    return apiFetch<{ hours: number; items: SshSource[] }>(
      '/network/ssh',
      signal ? { signal } : {},
    );
  },
  bans(signal) {
    return apiFetch<{ items: BanEntry[] }>('/network/bans', signal ? { signal } : {});
  },
  ban(ip, reason) {
    return apiFetch<BanResult>('/network/bans', {
      method: 'POST',
      body: reason ? { ip, reason } : { ip },
    });
  },
  pardon(ip) {
    return apiFetch<BanResult>(`/network/bans/${encodeURIComponent(ip)}/pardon`, {
      method: 'POST',
    });
  },
  scanners(window, signal) {
    return apiFetch<ScannerPreview>(
      `/network/scanners?window=${window}`,
      signal ? { signal } : {},
    );
  },
  banScanners(window, ips) {
    return apiFetch<ScannerBanResult>('/network/scanners/ban', {
      method: 'POST',
      body: ips ? { window, ips } : { window },
    });
  },
};

// ---- mock 实现 -----------------------------------------------------------

const MOCK_OVERVIEW: Overview = {
  edge: {
    enabled: true,
    connected: true,
    last_event_ts: '2026-10-09T09:50:00Z',
    error: null,
  },
  frpc: { status: 'active' },
  mc: { ok: true, online: 3, max: 20, latency_ms: 2.1, version: '1.21.1' },
  frps_host: '203.0.113.10',
  ports: [
    { port: 25565, proxy: 'minecraft', desc: 'Minecraft' },
    { port: 7000, proxy: 'frp', desc: 'frp 控制连接' },
  ],
};

const MOCK_IPS: IpRow[] = [
  {
    ip: '1.2.3.4',
    conns: 4,
    first_seen: '2026-10-09T08:00:00Z',
    last_seen: '2026-10-09T11:00:00Z',
    proxies: ['minecraft'],
    banned: false,
    tags: ['player'],
    players: ['Alice'],
    player_last_login: '2026-10-09T11:05:00Z',
  },
  {
    ip: '93.184.216.198',
    conns: 15,
    first_seen: '2026-10-09T02:00:00Z',
    last_seen: '2026-10-09T11:30:00Z',
    proxies: ['minecraft'],
    banned: false,
    tags: ['scanner'],
    players: [],
    player_last_login: null,
  },
  {
    ip: '203.0.113.9',
    conns: 3,
    first_seen: '2026-10-09T10:00:00Z',
    last_seen: '2026-10-09T11:40:00Z',
    proxies: ['ssh'],
    banned: false,
    tags: ['ssh'],
    players: [],
    player_last_login: null,
  },
  {
    ip: '198.51.100.66',
    conns: 2,
    first_seen: '2026-10-09T09:00:00Z',
    last_seen: '2026-10-09T09:30:00Z',
    proxies: ['minecraft'],
    banned: true,
    tags: ['banned'],
    players: [],
    player_last_login: null,
  },
];

const mockNetworkApi: NetworkApi = {
  async overview() {
    return MOCK_OVERVIEW;
  },
  async stats(window) {
    return {
      window,
      bucket: window === '1h' || window === '24h' ? 'hour' : 'day',
      by_proxy: {
        minecraft: { connections: 21, distinct_ips: 3 },
        ssh: { connections: 3, distinct_ips: 1 },
      },
      proxies: ['minecraft', 'ssh'],
      series: [
        { bucket: '2026-10-09T10:00', counts: { minecraft: 10, ssh: 1 } },
        { bucket: '2026-10-09T11:00', counts: { minecraft: 11, ssh: 2 } },
      ],
      tz_offset_min: 480,
    };
  },
  async ips(params) {
    let items = MOCK_IPS;
    if (params.tag) items = items.filter((i) => i.tags.includes(params.tag!));
    if (params.q) {
      const q = params.q.toLowerCase();
      items = items.filter(
        (i) => i.ip.toLowerCase().includes(q) || i.players.some((p) => p.toLowerCase().includes(q)),
      );
    }
    const offset = params.offset ?? 0;
    const limit = params.limit ?? 50;
    return {
      items: items.slice(offset, offset + limit),
      total: items.length,
      window: params.window ?? '24h',
      limit,
      offset,
    };
  },
  async recent(limit = 200) {
    const items: RecentConn[] = MOCK_IPS.slice(0, limit).map((i) => ({
      ts: i.last_seen,
      proxy: i.proxies[0] ?? 'minecraft',
      ip: i.ip,
      port: 50000,
      tags: i.tags,
      players: i.players,
    }));
    return { items };
  },
  async ssh() {
    return { hours: 24, items: [{ ip: '203.0.113.9', conns: 3, first_seen: null, last_seen: null }] };
  },
  async bans() {
    return {
      items: [
        {
          ip: '198.51.100.66',
          reason: '恶意扫描',
          created: '2026-10-01',
          source: 'cli',
          expires: null,
        },
      ],
    };
  },
  async ban(ip) {
    return { ip, ok: true, output: `OK: ban-ip ${ip}` };
  },
  async pardon(ip) {
    return { ip, ok: true, output: `OK: pardon-ip ${ip}` };
  },
  async scanners(window) {
    return {
      window,
      count: 1,
      items: [
        {
          ip: '93.184.216.198',
          conns: 15,
          first_seen: '2026-10-09T02:00:00Z',
          last_seen: '2026-10-09T11:30:00Z',
        },
      ],
    };
  },
  async banScanners(_window, ips) {
    const list = ips ?? ['93.184.216.198'];
    return {
      total: list.length,
      ok: list.length,
      results: list.map((ip) => ({ ip, ok: true })),
    };
  },
};

export const networkApi: NetworkApi =
  import.meta.env.VITE_MOCK === '1' ? mockNetworkApi : realNetworkApi;
