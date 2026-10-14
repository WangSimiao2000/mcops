import { apiFetch } from './http';

// ---- 类型 ----------------------------------------------------------------

export type LogLevel = 'INFO' | 'WARN' | 'ERROR';

export interface GameLogSource {
  name: string;
  date: string | null;
  seq: number;
  size: number;
  mtime: string;
  gzip: boolean;
  latest: boolean;
}

export interface CrashSource {
  name: string;
  size: number;
  mtime: string;
}

export interface JournalSource {
  name: 'agent' | 'server';
  unit: string;
  available: boolean;
  error: string | null;
}

export interface LogSources {
  game: GameLogSource[];
  crash: CrashSource[];
  journal: JournalSource[];
}

export interface LogLine {
  n: number;
  time: string | null;
  level: LogLevel | null;
  thread: string | null;
  text: string;
  continuation: boolean;
}

export interface LogView {
  lines: LogLine[];
  total: number;
  truncated: boolean;
}

export interface SearchHit {
  source: string;
  line: number;
  time: string | null;
  level: LogLevel | null;
  text: string;
}

export interface SearchResult {
  hits: SearchHit[];
  partial: boolean;
  scanned_from: string;
  scanned_to: string;
}

export interface ViewParams {
  source: string;
  tail?: number;
  offset?: number;
  limit?: number;
  level?: LogLevel[];
  q?: string;
  regex?: boolean;
}

export interface SearchParams {
  q: string;
  from?: string;
  to?: string;
  level?: LogLevel[];
  regex?: boolean;
}

export interface LogsApi {
  sources(signal?: AbortSignal): Promise<LogSources>;
  view(params: ViewParams, signal?: AbortSignal): Promise<LogView>;
  search(params: SearchParams, signal?: AbortSignal): Promise<SearchResult>;
  /** 下载 URL（直接作为 <a href>，带会话 cookie 由浏览器处理）。 */
  downloadUrl(source: string): string;
}

// ---- 真实实现 ------------------------------------------------------------

function viewQuery(p: ViewParams): string {
  const s = new URLSearchParams();
  s.set('source', p.source);
  if (p.tail !== undefined) s.set('tail', String(p.tail));
  if (p.offset !== undefined) s.set('offset', String(p.offset));
  if (p.limit !== undefined) s.set('limit', String(p.limit));
  if (p.level && p.level.length > 0) s.set('level', p.level.join(','));
  if (p.q) s.set('q', p.q);
  if (p.regex) s.set('regex', 'true');
  return s.toString();
}

const realLogsApi: LogsApi = {
  sources(signal) {
    return apiFetch<LogSources>('/logs/sources', signal ? { signal } : {});
  },
  view(params, signal) {
    return apiFetch<LogView>(`/logs/view?${viewQuery(params)}`, signal ? { signal } : {});
  },
  search(params, signal) {
    const s = new URLSearchParams();
    s.set('q', params.q);
    if (params.from) s.set('from', params.from);
    if (params.to) s.set('to', params.to);
    if (params.level && params.level.length > 0) s.set('level', params.level.join(','));
    if (params.regex) s.set('regex', 'true');
    return apiFetch<SearchResult>(`/logs/search?${s.toString()}`, signal ? { signal } : {});
  },
  downloadUrl(source) {
    return `/api/logs/download?source=${encodeURIComponent(source)}`;
  },
};

// ---- mock 实现 -----------------------------------------------------------

const MOCK_SOURCES: LogSources = {
  game: [
    {
      name: 'latest.log',
      date: '2026-10-09',
      seq: -1,
      size: 1024,
      mtime: '2026-10-09T09:50:00Z',
      gzip: false,
      latest: true,
    },
    {
      name: '2026-10-08-1.log.gz',
      date: '2026-10-08',
      seq: 1,
      size: 2048,
      mtime: '2026-10-08T23:59:00Z',
      gzip: true,
      latest: false,
    },
  ],
  crash: [{ name: 'crash-2026-10-07_10.00.00-server.txt', size: 4096, mtime: '2026-10-07T10:00:00Z' }],
  journal: [
    { name: 'agent', unit: 'mcops-agent.service', available: true, error: null },
    { name: 'server', unit: 'mc-server.service', available: true, error: null },
  ],
};

function mockLine(n: number, level: LogLevel, text: string): LogLine {
  return {
    n,
    time: `2026-10-09T10:00:${String(n % 60).padStart(2, '0')}+08:00`,
    level,
    thread: 'Server thread',
    text,
    continuation: false,
  };
}

const MOCK_LINES: LogLine[] = [
  mockLine(1, 'INFO', 'Done (1.0s)! For help, type "help"'),
  mockLine(2, 'WARN', "Can't keep up! Is the server overloaded?"),
  mockLine(3, 'ERROR', 'Encountered an unexpected exception'),
  { n: 4, time: null, level: null, thread: null, text: 'java.lang.RuntimeException: boom', continuation: true },
  mockLine(5, 'INFO', 'recovered and continuing'),
];

const mockLogsApi: LogsApi = {
  async sources() {
    return MOCK_SOURCES;
  },
  async view(params) {
    let lines = MOCK_LINES;
    if (params.level && params.level.length > 0) {
      const want = new Set(params.level);
      lines = lines.filter((l) => l.continuation || (l.level !== null && want.has(l.level)));
    }
    if (params.q) {
      const q = params.q.toLowerCase();
      lines = lines.filter((l) => l.text.toLowerCase().includes(q));
    }
    return { lines, total: lines.length, truncated: false };
  },
  async search(params) {
    const q = params.q.toLowerCase();
    const hits: SearchHit[] = MOCK_LINES.filter((l) => l.text.toLowerCase().includes(q)).map(
      (l) => ({ source: 'latest.log', line: l.n, time: l.time, level: l.level, text: l.text }),
    );
    return { hits, partial: false, scanned_from: '2026-10-03', scanned_to: '2026-10-09' };
  },
  downloadUrl(source) {
    return `/api/logs/download?source=${encodeURIComponent(source)}`;
  },
};

export const logsApi: LogsApi = import.meta.env.VITE_MOCK === '1' ? mockLogsApi : realLogsApi;
