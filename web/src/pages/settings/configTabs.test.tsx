import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { ServerConfigTab } from '@/pages/settings/ServerConfigTab';
import { GameRulesTab } from '@/pages/settings/GameRulesTab';
import type { Capabilities, ConfigResponse, PropertiesResponse } from '@/api/config';

vi.mock('@/api', () => ({
  api: {
    config: vi.fn(),
    updateConfig: vi.fn(),
    resetConfig: vi.fn(),
    properties: vi.fn(),
    updateProperties: vi.fn(),
  },
  configApi: {
    capabilities: vi.fn(),
  },
}));
import { api, configApi } from '@/api';

const mockApi = api as unknown as {
  config: ReturnType<typeof vi.fn>;
  updateConfig: ReturnType<typeof vi.fn>;
  resetConfig: ReturnType<typeof vi.fn>;
  properties: ReturnType<typeof vi.fn>;
  updateProperties: ReturnType<typeof vi.fn>;
};
const mockConfigApi = configApi as unknown as { capabilities: ReturnType<typeof vi.fn> };

function renderWith(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const caps = (over: Partial<Capabilities['features']> = {}): Capabilities => ({
  mc_version: '1.21.11',
  loader: 'fabric',
  loader_version: '0.19.2',
  mods: { easyauth: true, carpet: true, proxy_protocol: true, minimotd: true },
  proxy_source: '10.88.88.1',
  features: { edge: true, bot: true, restic: true, systemd: true, flight_watch: true, ...over },
});

function configFixture(): ConfigResponse {
  return {
    schema: {
      type: 'object',
      properties: {
        server: {
          type: 'object',
          title: '服务端',
          properties: {
            java_opts: { type: 'array', items: { type: 'string' }, title: 'JVM 参数', 'x-widget': 'memory', 'x-apply': 'mc' },
            port: { type: 'integer', title: '游戏端口', minimum: 1, maximum: 65535, 'x-apply': 'mc' },
          },
          required: ['java_opts', 'port'],
        },
        retention: {
          type: 'object',
          title: '数据保留',
          properties: {
            log_level: { type: 'string', enum: ['debug', 'info', 'warning'], title: '日志级别', 'x-apply': 'agent' },
          },
          required: ['log_level'],
        },
        bot: {
          type: 'object',
          title: 'QQ 机器人',
          properties: { enabled: { type: 'boolean', title: '启用机器人', 'x-apply': 'agent' } },
        },
        edge: {
          type: 'object',
          title: '边缘（frp）',
          properties: { bind_port: { type: 'integer', title: 'frps 控制端口', 'x-apply': 'agent' } },
        },
      },
    },
    sections: [
      { key: 'server', title: '服务端' },
      { key: 'retention', title: '数据保留' },
      { key: 'bot', title: 'QQ 机器人' },
      { key: 'edge', title: '边缘（frp）' },
    ],
    values: {
      server: { java_opts: ['-Xms8G', '-Xmx8G', '-XX:+UseG1GC'], port: 25565 },
      retention: { log_level: 'info' },
      bot: { enabled: true },
      edge: { bind_port: 7000 },
    },
    overridden: ['server.java_opts'],
    pending: [],
    readonly: { mode: 'control', api: '127.0.0.1:8765' },
  };
}

beforeEach(() => {
  mockApi.config.mockReset();
  mockApi.updateConfig.mockReset();
  mockApi.resetConfig.mockReset();
  mockApi.properties.mockReset();
  mockApi.updateProperties.mockReset();
  mockConfigApi.capabilities.mockReset();
  mockConfigApi.capabilities.mockResolvedValue(caps());
});
afterEach(() => cleanup());

describe('ServerConfigTab', () => {
  it('renders readonly info and schema-driven section cards with the right controls', async () => {
    mockApi.config.mockResolvedValue(configFixture());
    renderWith(<ServerConfigTab />);

    expect(await screen.findByText('运行信息（只读）')).toBeInTheDocument();
    expect(screen.getByText('127.0.0.1:8765')).toBeInTheDocument();
    // section titles
    expect(screen.getByText('服务端')).toBeInTheDocument();
    expect(screen.getByText('数据保留')).toBeInTheDocument();
    // memory widget: slider edits -Xms/-Xmx -> shows "均设为 8G"
    expect(screen.getByText(/-Xms\/-Xmx 均设为 8G/)).toBeInTheDocument();
    // enum control present with options
    const sel = screen.getByLabelText('内存 GB') as HTMLInputElement;
    expect(sel).toBeTruthy();
    // 已自定义 marker for the overridden java_opts
    expect(screen.getByText('已自定义')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复默认' })).toBeInTheDocument();
  });

  it('hides bot and edge sections when those features are off', async () => {
    mockApi.config.mockResolvedValue(configFixture());
    mockConfigApi.capabilities.mockResolvedValue(caps({ bot: false, edge: false }));
    renderWith(<ServerConfigTab />);

    await screen.findByText('服务端');
    await waitFor(() => expect(mockConfigApi.capabilities).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText('QQ 机器人')).not.toBeInTheDocument());
    expect(screen.queryByText('边缘（frp）')).not.toBeInTheDocument();
    // non-gated section still shows
    expect(screen.getByText('数据保留')).toBeInTheDocument();
  });

  it('saves only the changed leaf and sends the memory-widget java_opts', async () => {
    mockApi.config.mockResolvedValue(configFixture());
    mockApi.updateConfig.mockResolvedValue({
      ok: true,
      changed: [{ path: 'server.java_opts', apply: 'mc' }],
      pending: [{ path: 'server.java_opts', apply: 'mc' }],
      values: configFixture().values,
      overridden: ['server.java_opts'],
    });
    renderWith(<ServerConfigTab />);
    await screen.findByText(/-Xms\/-Xmx 均设为 8G/);

    const gb = screen.getByLabelText('内存 GB') as HTMLInputElement;
    fireEvent.change(gb, { target: { value: '12' } });

    // 服务端 card save button (first 保存)
    const saves = screen.getAllByRole('button', { name: '保存' });
    fireEvent.click(saves[0]);

    await waitFor(() => expect(mockApi.updateConfig).toHaveBeenCalledTimes(1));
    const [values] = mockApi.updateConfig.mock.calls[0];
    expect(values.server.java_opts).toEqual(['-Xms12G', '-Xmx12G', '-XX:+UseG1GC']);
    // port unchanged -> not in payload
    expect(values.server.port).toBeUndefined();
  });

  it('resets an overridden field back to default', async () => {
    mockApi.config.mockResolvedValue(configFixture());
    mockApi.resetConfig.mockResolvedValue({
      ok: true,
      changed: [{ path: 'server.java_opts', apply: 'mc' }],
      pending: [],
      values: configFixture().values,
      overridden: [],
    });
    renderWith(<ServerConfigTab />);
    fireEvent.click(await screen.findByRole('button', { name: '恢复默认' }));
    await waitFor(() => expect(mockApi.resetConfig).toHaveBeenCalledWith(['server.java_opts']));
  });
});

// ---- GameRulesTab ----------------------------------------------------------

function propsFixture(): PropertiesResponse {
  return {
    path: '/srv/server.properties',
    items: [
      { key: 'difficulty', value: 'normal', known: true, type: 'enum', options: ['peaceful', 'easy', 'normal', 'hard'], group: '玩法', title: '难度', managed: false },
      { key: 'view-distance', value: 12, known: true, type: 'int', min: 3, max: 32, group: '网络与性能', title: '视距', managed: false },
      { key: 'pvp', value: true, known: true, type: 'bool', group: '玩法', title: 'PVP', managed: false },
      { key: 'enable-rcon', value: true, known: true, type: 'bool', group: '网络与性能', title: '启用 RCON', managed: true, managed_reason: '由 mc-ops 管理' },
      { key: 'rcon.password', value: null, known: true, type: 'string', group: '网络与性能', title: 'RCON 密码', managed: true, sensitive: true },
      { key: 'custom-x', value: 'legacy', known: false, type: 'string', group: '其它', managed: false },
    ],
    pending: ['view-distance'],
  };
}

describe('GameRulesTab', () => {
  beforeEach(() => {
    mockApi.properties.mockReset();
    mockApi.updateProperties.mockReset();
  });

  it('renders grouped typed controls, hides sensitive keys, marks managed read-only', async () => {
    mockApi.properties.mockResolvedValue(propsFixture());
    renderWith(<GameRulesTab />);

    expect(await screen.findByLabelText('difficulty')).toBeInTheDocument();
    expect(screen.getByLabelText('view-distance')).toBeInTheDocument();
    // sensitive key never rendered
    expect(screen.queryByLabelText('rcon.password')).not.toBeInTheDocument();
    // managed key shown read-only (no input), with a managed badge
    expect(screen.queryByLabelText('enable-rcon')).not.toBeInTheDocument();
    expect(screen.getAllByText('mc-ops 管理').length).toBeGreaterThan(0);
    // unknown key in the 高级 group
    expect(screen.getByText('高级（其它键）')).toBeInTheDocument();
  });

  it('saves only the changed keys and shows the backup filename', async () => {
    mockApi.properties.mockResolvedValue(propsFixture());
    mockApi.updateProperties.mockResolvedValue({
      ok: true,
      changed: ['view-distance'],
      backup: 'server.properties.bak-20261010-140000',
    });
    renderWith(<GameRulesTab />);

    fireEvent.change(await screen.findByLabelText('view-distance'), { target: { value: '16' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockApi.updateProperties).toHaveBeenCalledTimes(1));
    expect(mockApi.updateProperties.mock.calls[0][0]).toEqual({ 'view-distance': 16 });
    expect(await screen.findByText(/server\.properties\.bak-20261010-140000/)).toBeInTheDocument();
  });
});
