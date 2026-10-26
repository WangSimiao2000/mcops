import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { MapRun, MapStatus } from '@/api/types';
import { MapCard } from '@/pages/ops/MapCard';
import { changedRegions, triggerLabel } from '@/lib/map';

vi.mock('@/api', () => ({ api: { mapStatus: vi.fn(), runMap: vi.fn() } }));
import { api } from '@/api';

const mockApi = api as unknown as { mapStatus: ReturnType<typeof vi.fn>; runMap: ReturnType<typeof vi.fn> };

const run = (over: Partial<MapRun> = {}): MapRun => ({
  started_at: '2026-10-17T05:03:00+08:00',
  finished_at: '2026-10-17T05:09:00+08:00',
  seconds: 360,
  trigger: 'restart',
  ok: true,
  error: null,
  result: { render: { regions: 422, rendered: { '1': 12, '0': 3 }, tiles: { '0': 500, '1': 1400 }, bytes: 128 * 2 ** 20 } },
  ...over,
});

function status(over: Partial<MapStatus> = {}): MapStatus {
  return {
    enabled: true, mode: 'restart', schedule: null, fallback_at: '06:30', running: false, phase: null,
    next_run: '2026-10-18T06:30:00+08:00', pushed: true, last: run(), history: [run()], last_error: null,
    tiles: null, public_url: 'https://blog.example.com/server/map/', has_preview: false, hot_minutes: 60, announce: true,
    ...over,
  };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MapCard /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('MapCard', () => {
  it('hides itself when the map is not configured', async () => {
    mockApi.mapStatus.mockResolvedValue({ enabled: false });
    const { container } = renderCard();
    await waitFor(() => expect(mockApi.mapStatus).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });

  it('shows the last run, tile totals and runs on demand', async () => {
    mockApi.mapStatus.mockResolvedValue(status());
    mockApi.runMap.mockResolvedValue({ accepted: true });
    renderCard();
    expect(await screen.findByText('跟随每日重启')).toBeInTheDocument();
    expect(screen.getByText('12 / 422 个区域')).toBeInTheDocument();
    expect(screen.getByText(/1900 张，128/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /打开公开地图/ })).toHaveAttribute('href', 'https://blog.example.com/server/map/');
    fireEvent.click(screen.getByRole('button', { name: '立即生成' }));
    await waitFor(() => expect(mockApi.runMap).toHaveBeenCalled());
  });

  it('shows the phase while running and the error of a failed run', async () => {
    const bad = run({ ok: false, error: 'RuntimeError: 渲染炸了', result: null });
    mockApi.mapStatus.mockResolvedValue(status({ running: true, phase: '渲染', history: [bad, run()] }));
    renderCard();
    expect(await screen.findByText('渲染')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成中…' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('渲染炸了');
  });

  it('labels triggers and counts changes', () => {
    expect(triggerLabel('web:admin')).toBe('面板（admin）');
    expect(triggerLabel('restart')).toBe('每日重启');
    expect(changedRegions(run())).toBe(12);
    expect(changedRegions(null)).toBeNull();
  });
});
