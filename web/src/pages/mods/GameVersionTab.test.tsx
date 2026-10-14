import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import type { GameVersions, Precheck } from '@/api/game';

vi.mock('@/api/game', () => ({
  gameApi: { versions: vi.fn(), precheck: vi.fn(), prepare: vi.fn(), upgrade: vi.fn() },
}));
import { gameApi } from '@/api/game';
import { GameVersionTab } from './GameVersionTab';

const api = gameApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

const VERSIONS: GameVersions = {
  current: { jar: 'a.jar', mc: '1.21.11', loader: '0.19.2', java_bin: '/usr/bin/java', java_major: 21 },
  games: [
    { version: '26.1', stable: true },
    { version: '1.21.11', stable: true },
  ],
  loaders: [{ version: '0.19.3', stable: true }],
  installer: '1.1.0',
  managed_javas: [],
  prepared: null,
  prepare_error: null,
  unit_managed: true,
};

const PLAN: Precheck = {
  current: { jar: 'a.jar', mc: '1.21.11', loader: '0.19.2', java_major: 21, java_bin: '/usr/bin/java' },
  target: { mc: '26.1', loader: '0.19.3', installer: '1.1.0', jar: 'b.jar', java_major: 25 },
  java: { required: 25, current: 21, needs_install: true },
  mods: [
    {
      file: 'l.jar',
      id: 'lithium',
      name: 'Lithium',
      version: '1',
      status: 'update',
      new_version: '2',
      new_file: 'l2.jar',
    },
    {
      file: 'p.jar',
      id: 'p',
      name: 'Private',
      version: '1',
      status: 'unknown',
      new_version: null,
      new_file: null,
    },
  ],
  blockers: [],
  warnings: [],
  fresh_install: false,
};

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GameVersionTab />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  for (const f of Object.values(api)) f.mockReset();
});
afterEach(cleanup);

describe('GameVersionTab', () => {
  it('检查兼容 → 准备 → 才能升级', async () => {
    api.versions!.mockResolvedValue(VERSIONS);
    api.precheck!.mockResolvedValue(PLAN);
    api.prepare!.mockResolvedValue({ accepted: true });
    api.upgrade!.mockResolvedValue({ accepted: true });
    renderTab();
    await screen.findByText('当前版本');
    fireEvent.change(screen.getByLabelText('Minecraft'), { target: { value: '26.1' } });
    fireEvent.click(screen.getByRole('button', { name: '检查兼容性' }));
    expect(await screen.findByText(/将自动安装 Temurin 25/)).toBeTruthy();
    expect(screen.getByText('找不到兼容版本')).toBeTruthy();
    const upgradeBtn = screen.getByRole('button', { name: /升级到 26.1/ }) as HTMLButtonElement;
    expect(upgradeBtn.disabled).toBe(true); // 没准备不能升级
    fireEvent.click(screen.getByLabelText(/升级时停用这 1 个/));
    fireEvent.click(screen.getByRole('button', { name: '1. 准备' }));
    await waitFor(() =>
      expect(api.prepare).toHaveBeenCalledWith(
        expect.objectContaining({ mc: '26.1', loader: '0.19.3', disable_unknown: true, java_mirror: 'tuna' }),
      ),
    );
    api.versions!.mockResolvedValue({
      ...VERSIONS,
      prepared: {
        target: { mc: '26.1', loader: '0.19.3', installer: '1.1.0' },
        jar: 'b.jar',
        java_bin: '/j',
        mod_updates: [],
        disable: ['p.jar'],
        prewarm_log_tail: '',
        prepared_at: '2026-10-10T00:00:00Z',
      },
    });
    await waitFor(
      () =>
        expect((screen.getByRole('button', { name: /升级到 26.1/ }) as HTMLButtonElement).disabled).toBe(
          false,
        ),
      {
        timeout: 5000,
      },
    );
    fireEvent.click(screen.getByRole('button', { name: /升级到 26.1/ }));
    fireEvent.click(await screen.findByRole('button', { name: '开始升级' }));
    await waitFor(() => expect(api.upgrade).toHaveBeenCalled());
  });

  it('有阻断项时不显示执行按钮', async () => {
    api.versions!.mockResolvedValue(VERSIONS);
    api.precheck!.mockResolvedValue({ ...PLAN, blockers: ['不支持降级'] });
    renderTab();
    await screen.findByText('当前版本');
    fireEvent.change(screen.getByLabelText('Minecraft'), { target: { value: '26.1' } });
    fireEvent.click(screen.getByRole('button', { name: '检查兼容性' }));
    expect(await screen.findByText(/无法升级：不支持降级/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: '1. 准备' })).toBeNull();
  });
});
