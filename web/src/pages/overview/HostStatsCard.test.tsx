import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { HostStats } from '@/api/system';
import { formatRate, formatUptimeShort, usageTone } from '@/lib/host';

vi.mock('@/api/system', () => ({ systemApi: { host: vi.fn() } }));
import { systemApi } from '@/api/system';
import { HostStatsCard } from './HostStatsCard';

const GB = 2 ** 30;
const STATS: HostStats = {
  interval_s: 10,
  history: [
    { ts: 1, cpu: 10, mem: 50, mc_rss: 7 * GB, mc_cpu: 40, rx: 1, tx: 2 },
    { ts: 2, cpu: 20, mem: 55, mc_rss: 7.2 * GB, mc_cpu: 45, rx: 1, tx: 3 },
  ],
  current: {
    ts: 2,
    cpu: { pct: 20, cores: 4, load: [5.2, 1, 1] },
    mem: { total: 16 * GB, available: 2 * GB, used: 14 * GB, swap_total: 0, swap_used: 0 },
    disks: [
      { labels: ['游戏目录', '冷备份'], path: '/home/x', total: 100 * GB, used: 95 * GB, free: 5 * GB },
    ],
    net: { rx_bps: 2048, tx_bps: 4096 },
    mc: { pid: 42, rss: 7.2 * GB, cpu_pct: 45 },
    uptime_s: 90061,
  },
};

afterEach(cleanup);

describe('host helpers', () => {
  it('usageTone', () => {
    expect(usageTone(null)).toBe('ok');
    expect(usageTone(74)).toBe('ok');
    expect(usageTone(80)).toBe('warn');
    expect(usageTone(95)).toBe('bad');
  });
  it('formatRate / formatUptimeShort', () => {
    expect(formatRate(null)).toBe('—');
    expect(formatRate(2048)).toBe('2 KB/s');
    expect(formatUptimeShort(90061)).toBe('1 天 1 小时');
    expect(formatUptimeShort(3700)).toBe('1 小时 1 分');
  });
});

describe('HostStatsCard', () => {
  it('显示 CPU/内存/MC 进程/磁盘，负载过高与磁盘接近满会标出', async () => {
    (systemApi.host as ReturnType<typeof vi.fn>).mockResolvedValue(STATS);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <HostStatsCard />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('20%')).toBeTruthy();
    expect(screen.getByText(/负载 5.20 .*（偏高）/)).toBeTruthy();
    expect(screen.getByText('14 GB / 16 GB')).toBeTruthy();
    expect(screen.getByText(/PID 42/)).toBeTruthy();
    expect(screen.getByText('磁盘 · 游戏目录、冷备份')).toBeTruthy();
    expect(screen.getByText(/（95%）/)).toBeTruthy();
    expect(screen.getAllByRole('img', { name: /最近一小时趋势/ }).length).toBeGreaterThan(0);
  });
});
