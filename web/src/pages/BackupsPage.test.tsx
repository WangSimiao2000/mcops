import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { BackupsPage } from '@/pages/BackupsPage';
import { ApiError } from '@/api/http';
import type { BackupsResponse } from '@/api/types';

vi.mock('@/api', () => ({
  api: {
    backups: vi.fn(),
    runJob: vi.fn(),
    restic: vi.fn(),
    restoreSnapshot: vi.fn(),
  },
}));
import { api } from '@/api';

const mockApi = api as unknown as {
  backups: ReturnType<typeof vi.fn>;
  restic: ReturnType<typeof vi.fn>;
  runJob: ReturnType<typeof vi.fn>;
  restoreSnapshot: ReturnType<typeof vi.fn>;
};

function renderPage(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const data: BackupsResponse = {
  items: [
    { name: 'mc-backup-20261009-050041.tar.gz', size: 486539264, mtime: '2026-10-09T05:02:08Z' },
    { name: 'mc-backup-20261008-211402.tar.gz', size: 471859200, mtime: '2026-10-08T21:14:37Z' },
  ],
};

beforeEach(() => {
  mockApi.backups.mockReset();
  mockApi.runJob.mockReset();
  mockApi.restic.mockReset();
  mockApi.restoreSnapshot.mockReset();
  mockApi.restic.mockResolvedValue({ enabled: false, snapshots: [], stats: null });
});
afterEach(() => cleanup());

describe('BackupsPage', () => {
  it('offers rate-limited download links for archives and snapshots', async () => {
    mockApi.backups.mockResolvedValue({ ...data, download_rate_kib: 512 });
    mockApi.restic.mockResolvedValue({
      enabled: true,
      snapshots: [
        { id: '0a1b2c3d', time: '2026-10-10T07:58:12Z', tags: ['hourly'], total_bytes: 3221225472, added_bytes: 1 },
      ],
      stats: null,
    });
    renderPage(<BackupsPage />);
    const link = await screen.findByRole('link', { name: '下载 mc-backup-20261009-050041.tar.gz' });
    expect(link).toHaveAttribute('href', '/api/backups/mc-backup-20261009-050041.tar.gz/download');
    expect(link).toHaveAttribute('download');
    // 464 MB @ 512 KiB/s ≈ 16 分钟
    expect(link).toHaveTextContent('约 16 分钟');
    const snap = await screen.findByRole('link', { name: '下载快照 0a1b2c3d（zip）' });
    expect(snap).toHaveAttribute('href', '/api/restic/0a1b2c3d/download');
    expect(screen.getByText(/限速 512 KiB\/s/)).toBeInTheDocument();
  });

  it('lists backups with human-readable sizes and a total', async () => {
    mockApi.backups.mockResolvedValue(data);
    renderPage(<BackupsPage />);
    expect(await screen.findByText('mc-backup-20261009-050041.tar.gz')).toBeInTheDocument();
    expect(screen.getByText('完整打包（2）')).toBeInTheDocument();
    expect(screen.getByText('464 MB')).toBeInTheDocument();
    // 486539264 + 471859200 = 958398464 -> 914 MB
    expect(screen.getByText(/总大小 914 MB/)).toBeInTheDocument();
  });

  it('clicking 立即备份 calls runJob("backup")', async () => {
    mockApi.backups.mockResolvedValue(data);
    mockApi.runJob.mockResolvedValue({ accepted: true });
    renderPage(<BackupsPage />);
    await screen.findByText('mc-backup-20261009-050041.tar.gz');
    fireEvent.click(screen.getByRole('button', { name: /立即备份/ }));
    await waitFor(() => expect(mockApi.runJob).toHaveBeenCalledWith('backup'));
  });

  it('shows a friendly notice on observe-mode 409', async () => {
    mockApi.backups.mockRejectedValue(new ApiError(409, '观测模式'));
    renderPage(<BackupsPage />);
    expect(await screen.findByText(/观测模式/)).toBeInTheDocument();
    expect(screen.queryByText(/立即备份/)).not.toBeInTheDocument();
  });

  it('empty list shows a placeholder', async () => {
    mockApi.backups.mockResolvedValue({ items: [] });
    renderPage(<BackupsPage />);
    expect(await screen.findByText(/点击上方「立即备份」生成第一个快照/)).toBeInTheDocument();
    expect(screen.getByText(/总大小 0 B/)).toBeInTheDocument();
  });

  it('shows restic snapshots and triggers restic_backup', async () => {
    mockApi.backups.mockResolvedValue(data);
    mockApi.runJob.mockResolvedValue({ accepted: true });
    mockApi.restic.mockResolvedValue({
      enabled: true,
      repo: '/r',
      snapshots: [
        { id: 'ab12cd34', time: '2026-10-09T07:00:00Z', tags: ['hourly'], total_bytes: 2 ** 30, added_bytes: 5 * 2 ** 20 },
      ],
      stats: { repo_bytes: 3 * 2 ** 30, uncompressed_bytes: null },
    });
    renderPage(<BackupsPage />);
    expect(await screen.findByText('ab12cd34')).toBeInTheDocument();
    expect(screen.getByText('增量快照（1）')).toBeInTheDocument();
    expect(screen.getByText(/仓库占用/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '立即快照' }));
    await waitFor(() => expect(mockApi.runJob).toHaveBeenCalledWith('restic_backup'));
  });

  it('switches to the NAS repo and restores a snapshot only after acknowledging', async () => {
    mockApi.backups.mockResolvedValue(data);
    mockApi.restoreSnapshot.mockResolvedValue({ accepted: true });
    const repos = [
      { id: 'local', name: '本机' },
      { id: 'nas', name: '局域网 NAS（SMB）' },
    ];
    mockApi.restic.mockImplementation(async (repo: string) => ({
      enabled: true,
      repo,
      repos,
      restore_running: null,
      warn_minutes: 5,
      snapshots: [
        {
          id: repo === 'nas' ? 'feed0001' : 'ab12cd34',
          time: '2026-10-09T07:00:00Z',
          tags: ['auto'],
          total_bytes: 2 ** 30,
          added_bytes: 1,
        },
      ],
      stats: null,
    }));
    renderPage(<BackupsPage />);
    expect(await screen.findByText('ab12cd34')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '局域网 NAS（SMB）' }));
    expect(await screen.findByText('feed0001')).toBeInTheDocument();
    expect(mockApi.restic).toHaveBeenCalledWith('nas', expect.anything());
    // 异地仓库不提供下载（经 SMB 现场导出太慢），只能回退
    expect(screen.queryByRole('link', { name: /下载快照/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '回退到快照 feed0001' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent('局域网 NAS（SMB）');
    expect(dialog).toHaveTextContent('倒计时 5 分钟');
    const go = screen.getByRole('button', { name: '开始回退' });
    expect(go).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: /世界 \+ Mod \+ 配置/ }));
    fireEvent.click(screen.getByRole('checkbox'));
    expect(go).toBeEnabled();
    fireEvent.click(go);
    await waitFor(() =>
      expect(mockApi.restoreSnapshot).toHaveBeenCalledWith('feed0001', 'nas', 'game'),
    );
    expect(await screen.findByText(/已开始回退到快照 feed0001/)).toBeInTheDocument();
  });

  it('disables restore while another job is running', async () => {
    mockApi.backups.mockResolvedValue(data);
    mockApi.restic.mockResolvedValue({
      enabled: true,
      repo: 'local',
      restore_running: 'scheduled_restart',
      snapshots: [
        { id: 'ab12cd34', time: '2026-10-09T07:00:00Z', tags: [], total_bytes: 1, added_bytes: 1 },
      ],
      stats: null,
    });
    renderPage(<BackupsPage />);
    expect(await screen.findByRole('button', { name: '回退到快照 ab12cd34' })).toBeDisabled();
    expect(screen.getByText(/定时重启/)).toBeInTheDocument();
  });
});
