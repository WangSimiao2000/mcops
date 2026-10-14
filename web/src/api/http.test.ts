import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, ApiError, setUnauthorizedHandler } from '@/api/http';
import { CSRF_HEADER } from '@/api/cookies';

function mockFetch(response: {
  status: number;
  body?: unknown;
}): ReturnType<typeof vi.fn> {
  const text = response.body === undefined ? null : JSON.stringify(response.body);
  const fn = vi.fn(async () =>
    new Response(text, {
      status: response.status,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

function setCookie(value: string) {
  Object.defineProperty(document, 'cookie', {
    configurable: true,
    get: () => value,
  });
}

describe('apiFetch', () => {
  beforeEach(() => {
    setCookie('mcops_csrf=tok-123; other=x');
    setUnauthorizedHandler(null);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('does NOT attach the CSRF header on GET', async () => {
    const fetchFn = mockFetch({ status: 200, body: { ok: true } });
    await apiFetch('/auth/me');
    const [, init] = fetchFn.mock.calls[0]!;
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers[CSRF_HEADER]).toBeUndefined();
    expect((init as RequestInit).credentials).toBe('same-origin');
  });

  it('attaches the CSRF header from the cookie on POST', async () => {
    const fetchFn = mockFetch({ status: 200, body: { accepted: true } });
    await apiFetch('/server/start', { method: 'POST' });
    const [url, init] = fetchFn.mock.calls[0]!;
    expect(url).toBe('/api/server/start');
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers[CSRF_HEADER]).toBe('tok-123');
    expect((init as RequestInit).method).toBe('POST');
  });

  it('serializes a JSON body and sets Content-Type', async () => {
    const fetchFn = mockFetch({ status: 200, body: { output: 'ok' } });
    await apiFetch('/server/command', { method: 'POST', body: { command: 'list' } });
    const [, init] = fetchFn.mock.calls[0]!;
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers['Content-Type']).toBe('application/json');
    expect((init as RequestInit).body).toBe(JSON.stringify({ command: 'list' }));
  });

  it('invokes the unauthorized handler on 401', async () => {
    mockFetch({ status: 401, body: { detail: 'unauthorized' } });
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(apiFetch('/system')).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('suppresses the unauthorized handler when asked (login flow)', async () => {
    mockFetch({ status: 401, body: { detail: 'totp_required' } });
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(
      apiFetch('/auth/login', { method: 'POST', body: { password: 'x' }, suppressUnauthorized: true }),
    ).rejects.toMatchObject({ status: 401, detail: 'totp_required' });
    expect(handler).not.toHaveBeenCalled();
  });

  it('returns undefined for 204', async () => {
    mockFetch({ status: 204 });
    const result = await apiFetch('/auth/logout', { method: 'POST' });
    expect(result).toBeUndefined();
  });

  it('throws ApiError carrying the detail on non-ok responses', async () => {
    mockFetch({ status: 409, body: { detail: '状态不允许' } });
    await expect(apiFetch('/server/stop', { method: 'POST' })).rejects.toMatchObject({
      status: 409,
      detail: '状态不允许',
    });
  });
});
