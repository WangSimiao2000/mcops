import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ModsOverview } from '@/api/mods';

// 测试真实实现是否打到正确的接口与方法。
const fetchMock = vi.fn();
vi.stubGlobal('fetch', fetchMock);

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const overview: ModsOverview = {
  mc_version: '1.21.11',
  loader_version: '0.19.2',
  loader_latest: '0.20.0',
  mods: [],
  issues: [],
  updates: {},
  checked_at: null,
  update_error: null,
  pending_restart: null,
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.resetModules();
});
afterEach(() => {
  vi.unstubAllEnvs();
});

async function loadReal() {
  vi.stubEnv('VITE_MOCK', '0');
  return (await import('@/api/mods')).modsApi;
}

describe('modsApi (real)', () => {
  it('overview GETs /api/mods', async () => {
    fetchMock.mockResolvedValue(jsonResponse(overview));
    const api = await loadReal();
    const r = await api.overview();
    expect(r.mc_version).toBe('1.21.11');
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/mods');
    expect((opts as RequestInit).method ?? 'GET').toBe('GET');
  });

  it('checkUpdates POSTs /api/mods/check-updates', async () => {
    fetchMock.mockResolvedValue(jsonResponse(overview));
    const api = await loadReal();
    await api.checkUpdates();
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/mods/check-updates');
    expect((opts as RequestInit).method).toBe('POST');
  });

  it('disable/enable/update/rollback hit the right paths', async () => {
    fetchMock.mockImplementation(async () => jsonResponse({}));
    const api = await loadReal();
    await api.disable('a b.jar');
    await api.enable('a b.jar');
    await api.update('a b.jar');
    await api.rollback('a b.jar');
    const urls = fetchMock.mock.calls.map((c) => c[0]);
    expect(urls).toEqual([
      '/api/mods/a%20b.jar/disable',
      '/api/mods/a%20b.jar/enable',
      '/api/mods/a%20b.jar/update',
      '/api/mods/a%20b.jar/rollback',
    ]);
    for (const c of fetchMock.mock.calls) {
      expect((c[1] as RequestInit).method).toBe('POST');
    }
  });

  it('updateAll POSTs /api/mods/update-all', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ results: [] }));
    const api = await loadReal();
    await api.updateAll();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/mods/update-all');
  });
});

describe('modsApi (mock)', () => {
  it('mock surface returns canned data without fetch', async () => {
    vi.stubEnv('VITE_MOCK', '1');
    const api = (await import('@/api/mods')).modsApi;
    const r = await api.overview();
    expect(r.mods.length).toBeGreaterThan(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
