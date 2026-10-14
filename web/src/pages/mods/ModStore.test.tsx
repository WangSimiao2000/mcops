import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';

import type { InstallPlan, SearchResult } from '@/api/modstore';
import { ApiError } from '@/api/http';

vi.mock('@/api/modstore', () => ({
  modStoreApi: {
    search: vi.fn(),
    versions: vi.fn(),
    plan: vi.fn(),
    install: vi.fn(),
    upload: vi.fn(),
    mrpackPlan: vi.fn(),
    mrpackApply: vi.fn(),
    configFiles: vi.fn(),
    configFile: vi.fn(),
    saveConfigFile: vi.fn(),
  },
}));
import { modStoreApi } from '@/api/modstore';
import { InstallTab } from './InstallTab';
import { ImportTab } from './ImportTab';
import { ConfigFilesTab } from './ConfigFilesTab';
import { localSyntaxError } from './util';

const api = modStoreApi as unknown as Record<string, ReturnType<typeof vi.fn>>;

function renderUi(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const SEARCH: SearchResult = {
  total: 1,
  offset: 0,
  limit: 20,
  mc_version: '1.21.11',
  hits: [
    {
      project_id: 'L',
      slug: 'lithium',
      title: 'Lithium',
      description: '性能优化',
      author: 'jelly',
      downloads: 23_000_000,
      icon_url: null,
      categories: [],
      client_side: 'optional',
      server_side: 'optional',
      latest_version: '1.21.11',
      date_modified: null,
      installed_file: null,
    },
  ],
};

const PLAN: InstallPlan = {
  mc_version: '1.21.11',
  install_count: 2,
  download_bytes: 2_000_000,
  warnings: [],
  items: [
    {
      project_id: 'L',
      title: 'Lithium',
      version_id: 'lv',
      version_number: '0.16',
      filename: 'lithium.jar',
      size: 1,
      reason: 'requested',
      required_by: [],
      status: 'install',
    },
    {
      project_id: 'F',
      title: 'Fabric API',
      version_id: 'fv',
      version_number: '0.130',
      filename: 'fapi.jar',
      size: 1,
      reason: 'dependency',
      required_by: ['Lithium'],
      status: 'install',
    },
  ],
};

beforeEach(() => {
  for (const f of Object.values(api)) f.mockReset();
});
afterEach(cleanup);

describe('InstallTab', () => {
  it('搜索 → 预览依赖 → 安装', async () => {
    api.search!.mockResolvedValue(SEARCH);
    api.versions!.mockResolvedValue({ items: [], mc_version: '1.21.11' });
    api.plan!.mockResolvedValue(PLAN);
    api.install!.mockResolvedValue({ installed: ['lithium.jar', 'fapi.jar'], plan: PLAN });
    renderUi(<InstallTab />);
    expect(await screen.findByText('Lithium')).toBeTruthy();
    expect(screen.getByText(/支持 Minecraft 1.21.11/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '安装' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/Lithium 的依赖/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: '安装 2 个' }));
    await waitFor(() => expect(api.install).toHaveBeenCalledWith([{ project_id: 'L', version_id: null }]));
    expect(await within(dialog).findByText(/已安装 2 个文件/)).toBeTruthy();
  });

  it('纯客户端 mod 不能安装', async () => {
    api.search!.mockResolvedValue(SEARCH);
    api.versions!.mockResolvedValue({ items: [], mc_version: '1.21.11' });
    api.plan!.mockResolvedValue({
      ...PLAN,
      install_count: 0,
      warnings: ['「Lithium」是纯客户端 mod'],
      items: [{ ...PLAN.items[0]!, status: 'client_only' }],
    });
    renderUi(<InstallTab />);
    fireEvent.click(await screen.findByRole('button', { name: '安装' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('纯客户端，跳过')).toBeTruthy();
    expect((within(dialog).getByRole('button', { name: /^安装/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('ImportTab', () => {
  it('上传 jar 显示后端校验错误', async () => {
    api.upload!.mockRejectedValue(new ApiError(400, '不是 Fabric mod（jar 里没有 fabric.mod.json）'));
    renderUi(<ImportTab />);
    const input = screen.getByLabelText('选择 jar 文件') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['x'], 'x.jar')] } });
    expect(await screen.findByText(/不是 Fabric mod/)).toBeTruthy();
  });
});

describe('ConfigFilesTab', () => {
  it('JSON 语法错误时禁止保存', async () => {
    api.configFiles!.mockResolvedValue({ items: [{ path: 'a.json', size: 8, mtime: '', editable: true }] });
    api.configFile!.mockResolvedValue({ path: 'a.json', content: '{"a": 1}', format: 'json', mtime: 5 });
    api.saveConfigFile!.mockResolvedValue({ path: 'a.json', backup: 'b', mtime: 6 });
    renderUi(<ConfigFilesTab />);
    fireEvent.click(await screen.findByText('a.json'));
    const ta = (await screen.findByLabelText('编辑 a.json')) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: '{"a": ' } });
    expect(screen.getByText(/JSON 语法错误/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(ta, { target: { value: '{"a": 2}' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(api.saveConfigFile).toHaveBeenCalledWith('a.json', '{"a": 2}', 5));
    expect(await screen.findByText(/已保存/)).toBeTruthy();
  });

  it('localSyntaxError 只检查 JSON', () => {
    expect(localSyntaxError('json', '{')).toMatch(/JSON/);
    expect(localSyntaxError('json', '{}')).toBeNull();
    expect(localSyntaxError('toml', 'a = = 1')).toBeNull();
  });
});
