import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { ConfigPendingBanner } from '@/components/ConfigPendingBanner';
import { ApiError } from '@/api/http';
import type { ConfigResponse } from '@/api/config';

vi.mock('@/api', () => ({
  api: {
    config: vi.fn(),
    restartAgent: vi.fn(),
    health: vi.fn(),
  },
}));
import { api } from '@/api';

const mockApi = api as unknown as {
  config: ReturnType<typeof vi.fn>;
  restartAgent: ReturnType<typeof vi.fn>;
  health: ReturnType<typeof vi.fn>;
};

function cfg(pending: ConfigResponse['pending']): ConfigResponse {
  return {
    schema: { type: 'object', properties: {} },
    sections: [],
    values: {},
    overridden: [],
    pending,
    readonly: {},
  };
}

function renderWith(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  mockApi.config.mockReset();
  mockApi.restartAgent.mockReset();
  mockApi.health.mockReset();
  vi.useRealTimers();
});
afterEach(() => cleanup());

describe('ConfigPendingBanner', () => {
  it('renders nothing when there is no pending change', async () => {
    mockApi.config.mockResolvedValue(cfg([]));
    const { container } = renderWith(<ConfigPendingBanner />);
    await waitFor(() => expect(mockApi.config).toHaveBeenCalled());
    expect(container.textContent).not.toMatch(/待应用/);
  });

  it('shows the agent-pending banner with the count and 立即应用 button', async () => {
    mockApi.config.mockResolvedValue(
      cfg([
        { path: 'ops.restart_time', apply: 'agent' },
        { path: 'bot.enabled', apply: 'agent' },
        { path: 'server.port', apply: 'mc' },
      ]),
    );
    renderWith(<ConfigPendingBanner />);
    expect(await screen.findByText(/有 2 项配置待应用/)).toBeInTheDocument();
    expect(screen.getByText(/另有 1 项将在下次开服生效/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '立即应用' })).toBeInTheDocument();
  });

  it('shows only a subtle note when all pending are mc-type', async () => {
    mockApi.config.mockResolvedValue(cfg([{ path: 'server.port', apply: 'mc' }]));
    renderWith(<ConfigPendingBanner />);
    expect(await screen.findByText(/1 项配置将在下次开服生效/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '立即应用' })).not.toBeInTheDocument();
  });

  it('confirms, restarts the agent, polls health, then clears', async () => {
    mockApi.config.mockResolvedValueOnce(cfg([{ path: 'ops.restart_time', apply: 'agent' }]));
    // after restart + invalidate, config reports no pending
    mockApi.config.mockResolvedValue(cfg([]));
    mockApi.restartAgent.mockResolvedValue({ accepted: true });
    mockApi.health.mockResolvedValue({ status: 'ok', version: 'x' });

    renderWith(<ConfigPendingBanner />);
    fireEvent.click(await screen.findByRole('button', { name: '立即应用' }));
    // ConfirmDialog
    fireEvent.click(await screen.findByRole('button', { name: '重启 agent' }));

    await waitFor(() => expect(mockApi.restartAgent).toHaveBeenCalledTimes(1), { timeout: 8000 });
    await waitFor(() => expect(mockApi.health).toHaveBeenCalled(), { timeout: 8000 });
  }, 15000);

  it('shows a manual-restart message on 409 (dev mode)', async () => {
    mockApi.config.mockResolvedValue(cfg([{ path: 'ops.restart_time', apply: 'agent' }]));
    mockApi.restartAgent.mockRejectedValue(new ApiError(409, 'not under systemd'));

    renderWith(<ConfigPendingBanner />);
    fireEvent.click(await screen.findByRole('button', { name: '立即应用' }));
    fireEvent.click(await screen.findByRole('button', { name: '重启 agent' }));

    expect(await screen.findByText(/手动重启 agent/)).toBeInTheDocument();
  });
});
