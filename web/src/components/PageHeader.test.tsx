import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { PageHeader } from '@/components/PageHeader';

afterEach(cleanup);

describe('PageHeader', () => {
  it('renders the title as a single h1', () => {
    render(<PageHeader title="网络" />);
    const h1 = screen.getByRole('heading', { level: 1 });
    expect(h1).toHaveTextContent('网络');
  });

  it('renders an optional subtitle', () => {
    render(<PageHeader title="备份" subtitle="每小时增量 + 每天冷备" />);
    expect(screen.getByText('每小时增量 + 每天冷备')).toBeInTheDocument();
  });

  it('omits the subtitle paragraph when none is given', () => {
    const { container } = render(<PageHeader title="玩家" />);
    expect(container.querySelectorAll('p')).toHaveLength(0);
  });

  it('renders actions alongside the title', () => {
    render(<PageHeader title="设置" actions={<button type="button">保存</button>} />);
    expect(screen.getByRole('button', { name: '保存' })).toBeInTheDocument();
  });
});
