import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { BotPage } from '@/pages/BotPage';
import type { GrantsResponse } from '@/api/types';

vi.mock('@/api', () => ({
  api: {
    botStatus: vi.fn().mockRejectedValue(new Error('x')),
    botApplications: vi.fn().mockResolvedValue({ items: [] }),
    botInteractions: vi.fn().mockResolvedValue({ items: [] }),
    botGrants: vi.fn(),
  },
}));

import { api } from '@/api';

const botGrants = (api as unknown as { botGrants: ReturnType<typeof vi.fn> }).botGrants;

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <BotPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe('BotPage grants card', () => {
  it('lists approved players with their registration deadline and outcome', async () => {
    const resp: GrantsResponse = {
      activation_hours: 48,
      items: [
        {
          name: 'NewPlayer01',
          openid_masked: 'A1B2…C3D4',
          granted_at: '2026-10-12T02:00:00Z',
          deadline: '2026-10-14T02:00:00Z',
          status: 'pending',
          resolved_at: null,
        },
        {
          name: 'LatePlayer',
          openid_masked: 'E5F6…7788',
          granted_at: '2026-10-09T02:00:00Z',
          deadline: '2026-10-11T02:00:00Z',
          status: 'expired',
          resolved_at: '2026-10-11T02:05:00Z',
        },
      ],
    };
    botGrants.mockResolvedValue(resp);
    renderPage();

    expect(await screen.findByText('获批待注册（1）')).toBeInTheDocument();
    expect(screen.getByText(/48 小时内须进服并完成注册/)).toBeInTheDocument();
    const region = screen.getByRole('region', { name: '获批记录' });
    const pending = within(region).getByText('NewPlayer01').closest('tr');
    expect(pending).not.toBeNull();
    expect(within(pending as HTMLElement).getByText('待注册')).toBeInTheDocument();
    expect(within(pending as HTMLElement).getByText(/前$/)).toBeInTheDocument();
    const late = within(region).getByText('LatePlayer').closest('tr');
    expect(within(late as HTMLElement).getByText('逾期已移出')).toBeInTheDocument();
  });

  it('says nothing expires when the deadline is disabled', async () => {
    botGrants.mockResolvedValue({ activation_hours: 0, items: [] });
    renderPage();
    expect(await screen.findByText(/未设置注册期限/)).toBeInTheDocument();
    expect(screen.getByText('暂无获批记录')).toBeInTheDocument();
  });
});
