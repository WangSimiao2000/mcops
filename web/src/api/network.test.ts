import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { networkApi } from '@/api/network';

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

function calledUrl(fn: ReturnType<typeof vi.fn>, i = 0): string {
  return String(fn.mock.calls[i]![0]);
}

function calledInit(fn: ReturnType<typeof vi.fn>, i = 0): RequestInit {
  return fn.mock.calls[i]![1] as RequestInit;
}

describe('networkApi (real impl)', () => {
  beforeEach(() => {
    Object.defineProperty(document, 'cookie', {
      configurable: true,
      get: () => 'mcops_csrf=tok',
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('overview hits /api/network/overview', async () => {
    const fn = mockFetch({});
    await networkApi.overview();
    expect(calledUrl(fn)).toBe('/api/network/overview');
  });

  it('stats passes the window', async () => {
    const fn = mockFetch({});
    await networkApi.stats('7d');
    expect(calledUrl(fn)).toBe('/api/network/stats?window=7d');
  });

  it('ips encodes window, q, tag, limit and offset', async () => {
    const fn = mockFetch({ items: [], total: 0, window: '24h', limit: 50, offset: 0 });
    await networkApi.ips({ window: '24h', q: '1.2.3', tag: 'scanner', limit: 20, offset: 40 });
    const url = calledUrl(fn);
    expect(url).toContain('/api/network/ips?');
    expect(url).toContain('window=24h');
    expect(url).toContain('q=1.2.3');
    expect(url).toContain('tag=scanner');
    expect(url).toContain('limit=20');
    expect(url).toContain('offset=40');
  });

  it('recent sends the limit', async () => {
    const fn = mockFetch({ items: [] });
    await networkApi.recent(200);
    expect(calledUrl(fn)).toBe('/api/network/recent?limit=200');
  });

  it('ban POSTs ip and reason with the CSRF header', async () => {
    const fn = mockFetch({ ip: '4.4.4.4', ok: true });
    await networkApi.ban('4.4.4.4', '恶意');
    expect(calledUrl(fn)).toBe('/api/network/bans');
    const init = calledInit(fn);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ ip: '4.4.4.4', reason: '恶意' });
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('tok');
  });

  it('ban omits reason when not given', async () => {
    const fn = mockFetch({ ip: '4.4.4.4', ok: true });
    await networkApi.ban('4.4.4.4');
    expect(JSON.parse(String(calledInit(fn).body))).toEqual({ ip: '4.4.4.4' });
  });

  it('pardon POSTs to the encoded ip path', async () => {
    const fn = mockFetch({ ip: '198.51.100.66', ok: true });
    await networkApi.pardon('198.51.100.66');
    expect(calledUrl(fn)).toBe('/api/network/bans/198.51.100.66/pardon');
    expect(calledInit(fn).method).toBe('POST');
  });

  it('scanners passes the window', async () => {
    const fn = mockFetch({ window: '24h', count: 0, items: [] });
    await networkApi.scanners('24h');
    expect(calledUrl(fn)).toBe('/api/network/scanners?window=24h');
  });

  it('banScanners POSTs window and optional ips', async () => {
    const fn = mockFetch({ total: 0, ok: 0, results: [] });
    await networkApi.banScanners('30d', ['1.1.1.1', '2.2.2.2']);
    expect(calledUrl(fn)).toBe('/api/network/scanners/ban');
    expect(JSON.parse(String(calledInit(fn).body))).toEqual({
      window: '30d',
      ips: ['1.1.1.1', '2.2.2.2'],
    });
  });

  it('banScanners omits ips when running the preview-driven batch', async () => {
    const fn = mockFetch({ total: 0, ok: 0, results: [] });
    await networkApi.banScanners('24h');
    expect(JSON.parse(String(calledInit(fn).body))).toEqual({ window: '24h' });
  });
});
