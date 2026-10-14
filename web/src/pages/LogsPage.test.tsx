import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { LogsPage } from '@/pages/LogsPage';
import type { LogSources, LogView, SearchResult } from '@/api/logs';

vi.mock('@/api/logs', () => ({
  logsApi: {
    sources: vi.fn(),
    view: vi.fn(),
    search: vi.fn(),
    downloadUrl: (s: string) => `/api/logs/download?source=${encodeURIComponent(s)}`,
  },
}));
import { logsApi } from '@/api/logs';

const mockApi = logsApi as unknown as {
  sources: ReturnType<typeof vi.fn>;
  view: ReturnType<typeof vi.fn>;
  search: ReturnType<typeof vi.fn>;
};

function renderPage(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function okSources(): LogSources {
  return {
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
    crash: [
      { name: 'crash-2026-10-07_10.00.00-server.txt', size: 4096, mtime: '2026-10-07T10:00:00Z' },
    ],
    journal: [
      { name: 'agent', unit: 'mcops-agent.service', available: true, error: null },
      { name: 'server', unit: 'mc-server.service', available: false, error: '不可用' },
    ],
  };
}

function okView(): LogView {
  return {
    lines: [
      { n: 1, time: '2026-10-09T10:00:01+08:00', level: 'INFO', thread: 'Server thread', text: 'hello world', continuation: false },
      { n: 2, time: '2026-10-09T10:00:02+08:00', level: 'ERROR', thread: 'Server thread', text: 'boom happened', continuation: false },
    ],
    total: 2,
    truncated: false,
  };
}

beforeEach(() => {
  mockApi.sources.mockReset();
  mockApi.view.mockReset();
  mockApi.search.mockReset();
  mockApi.sources.mockResolvedValue(okSources());
  mockApi.view.mockResolvedValue(okView());
  mockApi.search.mockResolvedValue({
    hits: [],
    partial: false,
    scanned_from: '2026-10-03',
    scanned_to: '2026-10-09',
  } satisfies SearchResult);
});
afterEach(() => cleanup());

describe('LogsPage', () => {
  it('renders the three source groups', async () => {
    renderPage(<LogsPage />);
    expect(await screen.findByText('游戏日志')).toBeInTheDocument();
    expect(screen.getByText('崩溃报告')).toBeInTheDocument();
    expect(screen.getByText('系统日志')).toBeInTheDocument();
    // 'latest.log（当前）' 同时出现在源按钮与查看器标题，用 getAllByText。
    expect(screen.getAllByText('latest.log（当前）').length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByText('crash-2026-10-07_10.00.00-server.txt'),
    ).toBeInTheDocument();
    // 默认加载 latest.log
    await waitFor(() => expect(mockApi.view).toHaveBeenCalled());
    expect(mockApi.view.mock.calls[0]![0]).toMatchObject({ source: 'latest.log' });
  });

  it('renders log lines in the viewer', async () => {
    renderPage(<LogsPage />);
    expect(await screen.findByText('hello world')).toBeInTheDocument();
    expect(screen.getByText('boom happened')).toBeInTheDocument();
  });

  it('toggling a level passes it to the view call', async () => {
    renderPage(<LogsPage />);
    await screen.findByText('hello world');
    fireEvent.click(screen.getByRole('button', { name: 'ERROR' }));
    await waitFor(() => {
      const last = mockApi.view.mock.calls.at(-1)![0];
      expect(last.level).toEqual(['ERROR']);
    });
  });

  it('typing a keyword passes q to the view call (debounced)', async () => {
    renderPage(<LogsPage />);
    await screen.findByText('hello world');
    fireEvent.change(screen.getByLabelText('关键词过滤'), { target: { value: 'boom' } });
    await waitFor(
      () => {
        const last = mockApi.view.mock.calls.at(-1)![0];
        expect(last.q).toBe('boom');
      },
      { timeout: 2000 },
    );
  });

  it('changing tail passes the new value', async () => {
    renderPage(<LogsPage />);
    await screen.findByText('hello world');
    fireEvent.change(screen.getByLabelText('tail 行数'), { target: { value: '2000' } });
    await waitFor(() => {
      const last = mockApi.view.mock.calls.at(-1)![0];
      expect(last.tail).toBe(2000);
    });
  });

  it('cross-file search sends q and jumps to the clicked hit', async () => {
    mockApi.search.mockResolvedValue({
      hits: [
        { source: '2026-10-08-1.log.gz', line: 42, time: '2026-10-08T20:00:00+08:00', level: 'WARN', text: 'found it' },
      ],
      partial: false,
      scanned_from: '2026-10-03',
      scanned_to: '2026-10-09',
    } satisfies SearchResult);
    renderPage(<LogsPage />);
    await screen.findByText('hello world');

    fireEvent.change(screen.getByLabelText('搜索关键词'), { target: { value: 'found' } });
    fireEvent.click(screen.getByRole('button', { name: '搜索' }));

    const hit = await screen.findByText('found it');
    await waitFor(() => expect(mockApi.search.mock.calls.at(-1)![0]).toMatchObject({ q: 'found' }));

    // 点击命中 => 查看器切换到该文件源。
    fireEvent.click(hit);
    await waitFor(() => {
      const last = mockApi.view.mock.calls.at(-1)![0];
      expect(last.source).toBe('2026-10-08-1.log.gz');
    });
  });

  it('download link points at the current source', async () => {
    renderPage(<LogsPage />);
    await screen.findByText('hello world');
    const link = screen.getByRole('button', { name: '下载' }).closest('a')!;
    expect(link).toHaveAttribute('href', '/api/logs/download?source=latest.log');
  });

  it('an unavailable journal source is disabled', async () => {
    renderPage(<LogsPage />);
    await screen.findByText('系统日志');
    const serverBtn = screen.getByRole('button', { name: /server（MC）/ });
    expect(serverBtn).toBeDisabled();
  });
});
