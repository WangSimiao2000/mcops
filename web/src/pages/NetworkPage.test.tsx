import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import { NetworkPage } from '@/pages/NetworkPage';
import type { IpList, Overview, ScannerPreview, Stats } from '@/api/network';
import type { SystemResponse } from '@/api/types';

vi.mock('@/api/network', () => ({
  networkApi: {
    overview: vi.fn(),
    stats: vi.fn(),
    ips: vi.fn(),
    recent: vi.fn(),
    ssh: vi.fn(),
    bans: vi.fn(),
    ban: vi.fn(),
    pardon: vi.fn(),
    scanners: vi.fn(),
    banScanners: vi.fn(),
  },
}));
vi.mock('@/api', () => ({
  api: { system: vi.fn() },
}));

import { networkApi } from '@/api/network';
import { api } from '@/api';

const net = networkApi as unknown as Record<string, ReturnType<typeof vi.fn>>;
const sysApi = api as unknown as { system: ReturnType<typeof vi.fn> };

function renderPage(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function okOverview(): Overview {
  return {
    edge: { enabled: true, connected: true, last_event_ts: '2026-10-09T09:50:00Z', error: null },
    frpc: { status: 'active' },
    mc: { ok: true, online: 3, max: 20, latency_ms: 2.1, version: '1.21.1' },
    frps_host: '203.0.113.10',
    ports: [
      { port: 25565, proxy: 'minecraft', desc: 'Minecraft' },
      { port: 7000, proxy: 'frp', desc: 'frp 控制连接' },
    ],
  };
}

function okStats(window: Stats['window'] = '24h'): Stats {
  return {
    window,
    bucket: 'hour',
    by_proxy: {
      minecraft: { connections: 21, distinct_ips: 3 },
      ssh: { connections: 3, distinct_ips: 1 },
    },
    proxies: ['minecraft', 'ssh'],
    series: [
      { bucket: '2026-10-09T10:00', counts: { minecraft: 10, ssh: 1 } },
      { bucket: '2026-10-09T11:00', counts: { minecraft: 11, ssh: 2 } },
    ],
    tz_offset_min: 480,
  };
}

function okIps(): IpList {
  return {
    items: [
      {
        ip: '1.2.3.4',
        conns: 4,
        first_seen: '2026-10-09T08:00:00Z',
        last_seen: '2026-10-09T11:00:00Z',
        proxies: ['minecraft'],
        banned: false,
        tags: ['player'],
        players: ['Alice'],
        player_last_login: '2026-10-09T11:05:00Z',
      },
      {
        ip: '198.51.100.66',
        conns: 2,
        first_seen: '2026-10-09T09:00:00Z',
        last_seen: '2026-10-09T09:30:00Z',
        proxies: ['minecraft'],
        banned: true,
        tags: ['banned'],
        players: [],
        player_last_login: null,
      },
    ],
    total: 2,
    window: '24h',
    limit: 100,
    offset: 0,
  };
}

function system(mode: SystemResponse['mode']): SystemResponse {
  return { version: '1.0.0', mode, modules: {} } as SystemResponse;
}

beforeEach(() => {
  for (const fn of Object.values(net)) fn.mockReset();
  sysApi.system.mockReset();
  sysApi.system.mockResolvedValue(system('control'));
  net.overview.mockResolvedValue(okOverview());
  net.stats.mockResolvedValue(okStats());
  net.ips.mockResolvedValue(okIps());
  net.recent.mockResolvedValue({ items: [] });
  net.ssh.mockResolvedValue({
    hours: 24,
    items: [{ ip: '203.0.113.9', conns: 3, first_seen: null, last_seen: null }],
  });
  net.bans.mockResolvedValue({
    items: [{ ip: '198.51.100.66', reason: '恶意', created: null, source: 'cli', expires: null }],
  });
  net.scanners.mockResolvedValue({
    window: '24h',
    count: 1,
    items: [{ ip: '93.184.216.198', conns: 15, first_seen: null, last_seen: null }],
  } satisfies ScannerPreview);
  net.ban.mockResolvedValue({ ip: '1.2.3.4', ok: true });
  net.pardon.mockResolvedValue({ ip: '198.51.100.66', ok: true });
  net.banScanners.mockResolvedValue({ total: 1, ok: 1, results: [{ ip: '93.184.216.198', ok: true }] });
});
afterEach(() => cleanup());

describe('NetworkPage', () => {
  it('renders topology, ports, stats and the IP table', async () => {
    renderPage(<NetworkPage />);
    expect(await screen.findByText('链路拓扑')).toBeInTheDocument();
    expect(screen.getByText('对外端口')).toBeInTheDocument();
    // 端口表内容
    expect(screen.getByText('25565')).toBeInTheDocument();
    // 统计卡
    expect(await screen.findByText('连接统计')).toBeInTheDocument();
    // IP 表里的玩家行
    expect(await screen.findByText('1.2.3.4')).toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
    // 时间轴按北京时间
    expect(await screen.findByText('时间：北京时间（UTC+8）')).toBeInTheDocument();
  });

  it('switching the time window refetches stats with the new window', async () => {
    renderPage(<NetworkPage />);
    await screen.findByText('连接统计');
    await waitFor(() => expect(net.stats).toHaveBeenCalledWith('24h', expect.anything()));
    fireEvent.click(screen.getByRole('button', { name: '7 天' }));
    await waitFor(() => expect(net.stats).toHaveBeenCalledWith('7d', expect.anything()));
    // IP 表也跟随窗口
    await waitFor(() =>
      expect(net.ips).toHaveBeenCalledWith(
        expect.objectContaining({ window: '7d' }),
        expect.anything(),
      ),
    );
  });

  it('filtering the IP table by tag passes the tag', async () => {
    renderPage(<NetworkPage />);
    await screen.findByText('1.2.3.4');
    fireEvent.change(screen.getByLabelText('按标签过滤'), { target: { value: 'scanner' } });
    await waitFor(() =>
      expect(net.ips).toHaveBeenCalledWith(
        expect.objectContaining({ tag: 'scanner' }),
        expect.anything(),
      ),
    );
  });

  it('banning an IP goes through the confirm dialog', async () => {
    renderPage(<NetworkPage />);
    const row = (await screen.findByText('1.2.3.4')).closest('tr')!;
    fireEvent.click(within(row).getByRole('button', { name: '封禁' }));
    // 二次确认
    expect(await screen.findByText('封禁 IP')).toBeInTheDocument();
    expect(net.ban).not.toHaveBeenCalled();
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '封禁' }));
    await waitFor(() => expect(net.ban).toHaveBeenCalledWith('1.2.3.4'));
  });

  it('one-click scanner ban confirms then calls banScanners', async () => {
    renderPage(<NetworkPage />);
    await screen.findByText('1.2.3.4');
    fireEvent.click(await screen.findByRole('button', { name: '一键封禁' }));
    expect(await screen.findByText('批量封禁扫描器')).toBeInTheDocument();
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '封禁' }));
    await waitFor(() => expect(net.banScanners).toHaveBeenCalledWith('24h'));
  });

  it('highlights recent SSH sources', async () => {
    renderPage(<NetworkPage />);
    expect(await screen.findByText(/SSH 来源/)).toBeInTheDocument();
    expect(screen.getByText('203.0.113.9')).toBeInTheDocument();
  });

  it('observe mode disables all write actions and shows a notice', async () => {
    sysApi.system.mockResolvedValue(system('observe'));
    renderPage(<NetworkPage />);
    await screen.findByText('1.2.3.4');
    await waitFor(() => expect(screen.getByText(/观测模式/)).toBeInTheDocument());
    const row = screen.getByText('1.2.3.4').closest('tr')!;
    expect(within(row).getByRole('button', { name: '封禁' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '一键封禁' })).toBeDisabled();
  });
});
