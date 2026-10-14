import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { PlayersCard } from '@/pages/overview/PlayersCard';
import type { ServerStatus } from '@/api/types';

vi.mock('@/api', () => ({
  api: { players: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
}));

afterEach(() => cleanup());

function renderCard(status: ServerStatus) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <PlayersCard status={status} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('PlayersCard', () => {
  it('lists fake players separately from real players', () => {
    renderCard({
      state: 'running', pid: 1, uptime_s: 10, mc_version: '1.21.11',
      players: {
        online: 3, max: 25, names: ['Yuki_Y', 'Zoe', 'Sandy'],
        real: ['Yuki_Y'], fake: ['Zoe', 'Sandy'], real_count: 1, fake_count: 2,
      },
    });
    expect(screen.getByText('在线玩家 (3/25（真人 1 · 假人 2）)')).toBeTruthy();
    const real = screen.getByRole('list', { name: '真人玩家' });
    expect(within(real).getByRole('link', { name: 'Yuki_Y' })).toBeTruthy();
    const fake = screen.getByRole('list', { name: '假人' });
    expect(within(fake).getAllByRole('listitem')).toHaveLength(2);
    expect(screen.queryByText('Anonymous Player')).toBeNull();
  });
});
