import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import { ModsPage } from '@/pages/ModsPage';
import { ApiError } from '@/api/http';
import type { ModsOverview } from '@/api/mods';

vi.mock('@/api/mods', () => ({
  modsApi: {
    overview: vi.fn(),
    checkUpdates: vi.fn(),
    disable: vi.fn(),
    enable: vi.fn(),
    update: vi.fn(),
    updateAll: vi.fn(),
    rollback: vi.fn(),
  },
}));
import { modsApi } from '@/api/mods';

const mockApi = modsApi as unknown as {
  overview: ReturnType<typeof vi.fn>;
  checkUpdates: ReturnType<typeof vi.fn>;
  disable: ReturnType<typeof vi.fn>;
  enable: ReturnType<typeof vi.fn>;
  update: ReturnType<typeof vi.fn>;
  updateAll: ReturnType<typeof vi.fn>;
  rollback: ReturnType<typeof vi.fn>;
};

function renderPage(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function okOverview(overrides: Partial<ModsOverview> = {}): ModsOverview {
  const base = {
    enabled: true,
    size: 1024,
    mtime: '2026-10-01T00:00:00Z',
    sha1: 'a',
    sha512: 'b',
    description: '',
    authors: [],
    homepage: null,
    depends: {},
    environment: '*',
    provides: [],
    kind: 'fabric' as const,
  };
  return {
    mc_version: '1.21.11',
    loader_version: '0.19.2',
    loader_latest: '0.20.0',
    mods: [
      { ...base, file: 'lithium.jar', id: 'lithium', name: 'Lithium', version: '0.12.0' },
      { ...base, file: 'oldmod.jar', id: 'oldmod', name: '旧 Mod', version: '1.0', enabled: false },
    ],
    issues: [{ level: 'error', mod: 'dup', message: '重复的 mod id' }],
    updates: {
      'lithium.jar': { source: 'modrinth', has_update: true, version_number: '0.13.0' },
    },
    checked_at: '2026-10-09T08:00:00Z',
    update_error: null,
    pending_restart: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockApi.overview.mockReset();
  mockApi.checkUpdates.mockReset();
  mockApi.disable.mockReset();
  mockApi.enable.mockReset();
  mockApi.update.mockReset();
  mockApi.updateAll.mockReset();
  mockApi.rollback.mockReset();
});
afterEach(() => cleanup());

describe('ModsPage', () => {
  it('renders version, issues and mod tables', async () => {
    mockApi.overview.mockResolvedValue(okOverview());
    renderPage(<ModsPage />);
    expect(await screen.findByText('Lithium')).toBeInTheDocument();
    expect(screen.getByText('1.21.11')).toBeInTheDocument();
    expect(screen.getByText('0.19.2')).toBeInTheDocument();
    expect(screen.getByText(/重复的 mod id/)).toBeInTheDocument();
    expect(screen.getByText('已启用（1）')).toBeInTheDocument();
    expect(screen.getByText('已停用（1）')).toBeInTheDocument();
    expect(screen.getByText(/可更新 → 0.13.0/)).toBeInTheDocument();
  });

  it('toggling an enabled mod calls disable', async () => {
    mockApi.overview.mockResolvedValue(okOverview());
    mockApi.disable.mockResolvedValue({});
    renderPage(<ModsPage />);
    await screen.findByText('Lithium');
    fireEvent.click(screen.getByRole('checkbox', { name: '停用 lithium.jar' }));
    await waitFor(() => expect(mockApi.disable).toHaveBeenCalledWith('lithium.jar'));
    expect(mockApi.enable).not.toHaveBeenCalled();
  });

  it('toggling a disabled mod calls enable', async () => {
    mockApi.overview.mockResolvedValue(okOverview());
    mockApi.enable.mockResolvedValue({});
    renderPage(<ModsPage />);
    await screen.findByText('旧 Mod');
    fireEvent.click(screen.getByRole('checkbox', { name: '启用 oldmod.jar' }));
    await waitFor(() => expect(mockApi.enable).toHaveBeenCalledWith('oldmod.jar'));
  });

  it('全部更新 requires a confirm before calling updateAll', async () => {
    mockApi.overview.mockResolvedValue(okOverview());
    mockApi.updateAll.mockResolvedValue({ results: [{ file: 'lithium.jar', ok: true }] });
    renderPage(<ModsPage />);
    await screen.findByText('Lithium');

    fireEvent.click(screen.getByRole('button', { name: /全部更新/ }));
    expect(mockApi.updateAll).not.toHaveBeenCalled();

    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('更新全部可升级的 mod？')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '全部更新' }));
    await waitFor(() => expect(mockApi.updateAll).toHaveBeenCalledTimes(1));
  });

  it('检查更新 calls checkUpdates and shows last-checked time', async () => {
    mockApi.overview.mockResolvedValue(okOverview());
    mockApi.checkUpdates.mockResolvedValue(okOverview());
    renderPage(<ModsPage />);
    await screen.findByText('Lithium');
    expect(screen.getByText(/上次检查/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '检查更新' }));
    await waitFor(() => expect(mockApi.checkUpdates).toHaveBeenCalledTimes(1));
  });

  it('shows a pending-restart banner with a link to overview', async () => {
    mockApi.overview.mockResolvedValue(
      okOverview({
        pending_restart: { since: '2026-10-09T08:00:00Z', changes: [{ action: 'disable', file: 'x.jar' }] },
      }),
    );
    renderPage(<ModsPage />);
    expect(await screen.findByText(/需要重启服务器才能生效/)).toBeInTheDocument();
    expect(screen.getByText('停用 x.jar')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /前往总览页重启/ })).toBeInTheDocument();
  });

  it('filters mods by name', async () => {
    mockApi.overview.mockResolvedValue(okOverview());
    renderPage(<ModsPage />);
    await screen.findByText('Lithium');
    fireEvent.change(screen.getByLabelText('过滤 mod'), { target: { value: 'lithium' } });
    await waitFor(() => expect(screen.queryByText('旧 Mod')).not.toBeInTheDocument());
    expect(screen.getByText('Lithium')).toBeInTheDocument();
  });

  it('shows a friendly notice on observe-mode 409', async () => {
    mockApi.overview.mockRejectedValue(new ApiError(409, '观测模式：mod 的启停与升级暂由手动操作，面板不接管'));
    renderPage(<ModsPage />);
    expect(await screen.findByText(/观测模式/)).toBeInTheDocument();
    expect(screen.queryByText('检查更新')).not.toBeInTheDocument();
  });

  it('shows an error when an action fails with ApiError', async () => {
    mockApi.overview.mockResolvedValue(okOverview());
    mockApi.disable.mockRejectedValue(new ApiError(400, '未知的 mod 文件'));
    renderPage(<ModsPage />);
    await screen.findByText('Lithium');
    fireEvent.click(screen.getByRole('checkbox', { name: '停用 lithium.jar' }));
    expect(await screen.findByText('未知的 mod 文件')).toBeInTheDocument();
  });
});
