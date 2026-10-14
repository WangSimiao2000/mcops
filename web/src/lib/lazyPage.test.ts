import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loadPage } from './lazyPage';

function Page() {
  return null;
}

beforeEach(() => sessionStorage.clear());

describe('loadPage', () => {
  it('resolves the named export', async () => {
    const mod = await loadPage(() => Promise.resolve({ Page }), 'Page');
    expect(mod.default).toBe(Page);
  });

  it('reloads once when a stale chunk fails to load, then gives up', async () => {
    const reload = vi.fn();
    const fail = () => Promise.reject(new TypeError('Failed to fetch dynamically imported module'));
    const pending = loadPage(fail, 'Page' as never, reload);
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    void pending;
    await expect(loadPage(fail, 'Page' as never, reload)).rejects.toThrow('Failed to fetch');
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('clears the guard after a successful load', async () => {
    sessionStorage.setItem('mcops-chunk-reload', '1');
    await loadPage(() => Promise.resolve({ Page }), 'Page');
    expect(sessionStorage.getItem('mcops-chunk-reload')).toBeNull();
  });
});
