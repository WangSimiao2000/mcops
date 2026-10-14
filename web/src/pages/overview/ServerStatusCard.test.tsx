import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { ServerStatusCard } from '@/pages/overview/ServerStatusCard';
import type { ServerStatus } from '@/api/types';

vi.mock('@/api', () => ({
  api: {
    serverStart: vi.fn(),
    serverStop: vi.fn(),
    serverRestart: vi.fn(),
  },
}));
import { api } from '@/api';

const mockApi = api as unknown as {
  serverStart: ReturnType<typeof vi.fn>;
  serverStop: ReturnType<typeof vi.fn>;
  serverRestart: ReturnType<typeof vi.fn>;
};

function renderCard(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const running: ServerStatus = {
  state: 'running', pid: 1234, uptime_s: 3600, mc_version: '1.21.11',
  players: { online: 1, max: 20, names: ['OpAdmin'] },
};

beforeEach(() => {
  mockApi.serverStart.mockReset().mockResolvedValue({ accepted: true });
  mockApi.serverStop.mockReset().mockResolvedValue({ accepted: true });
  mockApi.serverRestart.mockReset().mockResolvedValue({ accepted: true });
});
afterEach(() => cleanup());

describe('ServerStatusCard', () => {
  it('control mode: stop requires confirm, then calls serverStop', async () => {
    renderCard(<ServerStatusCard status={running} mode="control" />);
    fireEvent.click(screen.getByRole('button', { name: '停止' }));
    expect(mockApi.serverStop).not.toHaveBeenCalled();
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: '停止' }));
    await waitFor(() => expect(mockApi.serverStop).toHaveBeenCalledTimes(1));
  });

  it('control mode: start is disabled while running, enabled when stopped', () => {
    const { rerender } = renderCard(<ServerStatusCard status={running} mode="control" />);
    expect(screen.getByRole('button', { name: '启动' })).toBeDisabled();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <ServerStatusCard status={{ ...running, state: 'stopped' }} mode="control" />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('button', { name: '启动' })).toBeEnabled();
  });

  it('observe mode: controls disabled and a notice is shown', () => {
    renderCard(<ServerStatusCard status={running} mode="observe" />);
    expect(screen.getByText(/观测模式/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '启动' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '停止' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重启' })).toBeDisabled();
  });
});
