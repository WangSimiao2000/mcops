import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppLayout } from '@/components/AppLayout';

vi.mock('@/auth/useAuth', () => ({ useAuth: () => ({ user: 'admin', logout: vi.fn() }) }));
vi.mock('@/console/useConsole', () => ({
  useConsole: () => ({ connection: 'open', lastAlert: null }),
}));
vi.mock('@/api', () => ({ api: { alerts: vi.fn().mockResolvedValue({ items: [], total: 0 }) } }));

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<p>overview</p>} />
            <Route path="/network" element={<p>network body</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(cleanup);

describe('AppLayout', () => {
  it('sets a per-page document title', () => {
    renderAt('/network');
    expect(document.title).toBe('网络 · mc-ops');
  });

  it('opens the mobile drawer from the menu button and closes it on navigation', () => {
    renderAt('/');
    const toggle = screen.getByRole('button', { name: '打开菜单' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    // Desktop sidebar nav is always in the DOM (hidden by CSS below md).
    expect(screen.getAllByRole('navigation', { name: '主导航' })).toHaveLength(1);

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const navs = screen.getAllByRole('navigation', { name: '主导航' });
    expect(navs).toHaveLength(2);

    fireEvent.click(screen.getAllByRole('link', { name: '网络' })[1]);
    expect(screen.getByText('network body')).toBeInTheDocument();
    expect(screen.getAllByRole('navigation', { name: '主导航' })).toHaveLength(1);
  });

  it('closes the drawer on Escape', () => {
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: '打开菜单' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getAllByRole('navigation', { name: '主导航' })).toHaveLength(1);
  });
});
