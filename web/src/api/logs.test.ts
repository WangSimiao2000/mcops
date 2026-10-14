import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { logsApi } from '@/api/logs';

function mockFetch(body: unknown): ReturnType<typeof vi.fn> {
  const fn = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

function calledUrl(fn: ReturnType<typeof vi.fn>): string {
  return String(fn.mock.calls[0]![0]);
}

describe('logsApi (real impl)', () => {
  beforeEach(() => {
    Object.defineProperty(document, 'cookie', { configurable: true, get: () => '' });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('sources hits /api/logs/sources', async () => {
    const fn = mockFetch({ game: [], crash: [], journal: [] });
    await logsApi.sources();
    expect(calledUrl(fn)).toBe('/api/logs/sources');
  });

  it('view encodes source, tail, levels and keyword', async () => {
    const fn = mockFetch({ lines: [], total: 0, truncated: false });
    await logsApi.view({
      source: 'latest.log',
      tail: 2000,
      level: ['WARN', 'ERROR'],
      q: 'needle',
    });
    const url = calledUrl(fn);
    expect(url).toContain('/api/logs/view?');
    expect(url).toContain('source=latest.log');
    expect(url).toContain('tail=2000');
    expect(url).toContain('level=WARN%2CERROR');
    expect(url).toContain('q=needle');
    expect(url).not.toContain('regex=');
  });

  it('view adds regex=true only when requested', async () => {
    const fn = mockFetch({ lines: [], total: 0, truncated: false });
    await logsApi.view({ source: 'latest.log', q: '\\d+', regex: true });
    expect(calledUrl(fn)).toContain('regex=true');
  });

  it('view sends offset/limit for paging', async () => {
    const fn = mockFetch({ lines: [], total: 0, truncated: false });
    await logsApi.view({ source: 'latest.log', offset: 10, limit: 5 });
    const url = calledUrl(fn);
    expect(url).toContain('offset=10');
    expect(url).toContain('limit=5');
  });

  it('search encodes q, from, to and level', async () => {
    const fn = mockFetch({ hits: [], partial: false, scanned_from: '', scanned_to: '' });
    await logsApi.search({
      q: 'boom',
      from: '2026-10-01',
      to: '2026-10-07',
      level: ['ERROR'],
    });
    const url = calledUrl(fn);
    expect(url).toContain('/api/logs/search?');
    expect(url).toContain('q=boom');
    expect(url).toContain('from=2026-10-01');
    expect(url).toContain('to=2026-10-07');
    expect(url).toContain('level=ERROR');
  });

  it('downloadUrl encodes the source for crash/journal names', () => {
    expect(logsApi.downloadUrl('2026-10-07-1.log.gz')).toBe(
      '/api/logs/download?source=2026-10-07-1.log.gz',
    );
    expect(logsApi.downloadUrl('crash:crash-a b.txt')).toBe(
      '/api/logs/download?source=crash%3Acrash-a%20b.txt',
    );
  });
});
