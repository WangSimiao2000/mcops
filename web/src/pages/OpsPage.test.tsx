import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { OpsPage } from '@/pages/OpsPage';
import { ApiError } from '@/api/http';
import type { JobsResponse } from '@/api/types';

// Mock the api surface the page imports.
vi.mock('@/api', () => ({
  api: {
    jobs: vi.fn(),
    runJob: vi.fn(),
    watchdogReset: vi.fn(),
  },
}));
import { api } from '@/api';

const mockApi = api as unknown as {
  jobs: ReturnType<typeof vi.fn>;
  runJob: ReturnType<typeof vi.fn>;
  watchdogReset: ReturnType<typeof vi.fn>;
};

function renderPage(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function okJobs(overrides: Partial<JobsResponse> = {}): JobsResponse {
  return {
    jobs: [
      { id: 'scheduled_restart', name: '定时重启 + 冷备份', schedule: '05:00', next_run: '2099-01-01T05:00:00Z' },
      { id: 'backup', name: '立即备份（不停服）', schedule: null, next_run: null },
    ],
    running: null,
    watchdog: { suspended: false, recent_crashes: 0 },
    history: [
      {
        id: 10, job: 'scheduled_restart', trigger: 'schedule',
        started_at: '2026-10-09T05:00:00Z', finished_at: '2026-10-09T05:02:00Z',
        status: 'ok', log: '关服\n冷备份\n开服完成', result: { backup: 'x.tar.gz' },
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  mockApi.jobs.mockReset();
  mockApi.runJob.mockReset();
  mockApi.watchdogReset.mockReset();
});
afterEach(() => cleanup());

describe('OpsPage', () => {
  it('shows live progress and ETA for a running restore', async () => {
    mockApi.jobs.mockResolvedValue(
      okJobs({
        running: 'snapshot_restore',
        live: {
          job: 'snapshot_restore',
          run_id: 11,
          started_at: '2026-10-13T02:25:50Z',
          log: '10:25:50 目标：局域网 NAS（SMB）快照 47da067d\n10:31:04 从局域网 NAS（SMB）解出快照（47da067d）',
          progress: {
            label: '从局域网 NAS（SMB）解出快照',
            done_bytes: 2 * 1024 ** 3,
            total_bytes: 4 * 1024 ** 3,
            percent: 50,
            rate_bps: 4 * 1024 ** 2,
            eta_s: 512,
            elapsed_s: 540,
          },
        },
      }),
    );
    renderPage(<OpsPage />);
    const bar = await screen.findByRole('progressbar', { name: '从局域网 NAS（SMB）解出快照' });
    expect(bar).toHaveAttribute('aria-valuenow', '50');
    const panel = screen.getByRole('region', { name: '正在运行：从快照回退' });
    expect(within(panel).getByText(/50\.0% · 2 GB \/ 4 GB/)).toBeInTheDocument();
    expect(within(panel).getByText(/4 MB\/s · 预计还要 8 分 32 秒 · 已用 9 分/)).toBeInTheDocument();
    // 状态行显示最新一步（去掉时间戳），完整日志在可展开的「实时日志」里
    expect(within(panel).getByText(/：从局域网 NAS（SMB）解出快照（47da067d）/)).toBeInTheDocument();
    expect(within(panel).getByText("实时日志（2 行）")).toBeInTheDocument();
  });

  it('renders job cards and run history', async () => {
    mockApi.jobs.mockResolvedValue(okJobs());
    renderPage(<OpsPage />);
    // 任务名现在同时出现在卡片标题与运行历史「任务」列（历史用 jobDisplayName 展示中文）。
    expect((await screen.findAllByText('定时重启 + 冷备份')).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('立即备份（不停服）')).toBeInTheDocument();
    expect(screen.getByText('运行历史（1）')).toBeInTheDocument();
    expect(screen.getByText('成功')).toBeInTheDocument();
  });

  it('scheduled_restart requires a confirm before calling runJob', async () => {
    mockApi.jobs.mockResolvedValue(okJobs());
    mockApi.runJob.mockResolvedValue({ accepted: true });
    renderPage(<OpsPage />);

    // 卡片标题是 <h2>，用它定位卡片（历史列也有同名文本）。
    const heading = await screen.findByRole('heading', { name: '定时重启 + 冷备份' });
    const card = heading.closest('div[class*="rounded-lg"]')!;
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: '立即运行' }));

    // Not called yet — a confirm dialog must appear first.
    expect(mockApi.runJob).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('立即执行定时重启 + 冷备份？')).toBeInTheDocument();
    expect(within(dialog).getByText(/停服约 2 分钟/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '立即运行' }));
    await waitFor(() => expect(mockApi.runJob).toHaveBeenCalledWith('scheduled_restart'));
  });

  it('backup runs immediately without a confirm', async () => {
    mockApi.jobs.mockResolvedValue(okJobs());
    mockApi.runJob.mockResolvedValue({ accepted: true });
    renderPage(<OpsPage />);

    const card = (await screen.findByText('立即备份（不停服）')).closest('div[class*="rounded-lg"]')!;
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: '立即运行' }));
    await waitFor(() => expect(mockApi.runJob).toHaveBeenCalledWith('backup'));
    expect(screen.queryByText('立即执行定时重启 + 冷备份？')).not.toBeInTheDocument();
  });

  it('shows a friendly notice on observe-mode 409', async () => {
    mockApi.jobs.mockRejectedValue(new ApiError(409, '观测模式：定时任务与备份仍由旧脚本负责'));
    renderPage(<OpsPage />);
    expect(await screen.findByText(/观测模式/)).toBeInTheDocument();
    expect(screen.queryByText('运行历史（0）')).not.toBeInTheDocument();
  });

  it('shows the suspended watchdog alert and calls reset', async () => {
    mockApi.jobs.mockResolvedValue(
      okJobs({ watchdog: { suspended: true, recent_crashes: 3 } }),
    );
    mockApi.watchdogReset.mockResolvedValue({ ok: true });
    renderPage(<OpsPage />);

    expect(await screen.findByText(/看门狗已熔断/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '恢复自动重启' }));
    await waitFor(() => expect(mockApi.watchdogReset).toHaveBeenCalledTimes(1));
  });

  it('shows a running banner when a job is in flight', async () => {
    mockApi.jobs.mockResolvedValue(okJobs({ running: 'backup' }));
    renderPage(<OpsPage />);
    expect(await screen.findByText(/正在运行/)).toBeInTheDocument();
  });
});
