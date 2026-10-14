import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';
import { SettingsPage } from '@/pages/SettingsPage';
import { ApiError } from '@/api/http';
import type { NotifySettings } from '@/api/types';

vi.mock('@/api', () => ({
  api: {
    settingsNotify: vi.fn(),
    updateSettingsNotify: vi.fn(),
    testNotify: vi.fn(),
  },
  configApi: {
    // The notify tab prefetches capabilities; default-show on failure.
    capabilities: vi.fn().mockRejectedValue(new Error('no caps in this test')),
  },
}));
import { api } from '@/api';

const mockApi = api as unknown as {
  settingsNotify: ReturnType<typeof vi.fn>;
  updateSettingsNotify: ReturnType<typeof vi.fn>;
  testNotify: ReturnType<typeof vi.fn>;
};

function renderPage(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function okData(overrides: Partial<NotifySettings> = {}): NotifySettings {
  return {
    email: {
      enabled: true,
      host: 'smtp.qq.com',
      port: 465,
      security: 'ssl',
      username: '',
      sender: 'mc@x.com',
      to: ['admin@x.com'],
      severities: ['critical', 'warning'],
      max_per_hour: 20,
      has_password: true,
    },
    qq: {
      enabled: true,
      severities: ['critical'],
      groups: [{ openid: 'OPENID_AAA', label: '管理群' }],
    },
    rules: [
      {
        id: 'night_login',
        name: '凌晨登录',
        description: '01:00–06:00 登录',
        default_severity: 'warning',
        enabled: true,
        severity: null,
      },
      {
        id: 'flight_suspect',
        name: '疑似飞行',
        description: '异常飞行',
        default_severity: 'warning',
        enabled: true,
        severity: null,
      },
    ],
    known_groups: [
      { openid: 'OPENID_AAA', openid_masked: 'OPEN..._AAA', added_at: '2026-08-02T10:00:00Z', is_alert: true },
      { openid: 'OPENID_BBB', openid_masked: 'OPEN..._BBB', added_at: null, is_alert: false },
    ],
    bot: { enabled: true, connected: true },
    stats: {
      email_sent_hour: 3,
      email_dropped: 1,
      qq_sent_today: 7,
      last_error: { email: null, qq: null },
    },
    ...overrides,
  };
}

beforeEach(() => {
  mockApi.settingsNotify.mockReset();
  mockApi.updateSettingsNotify.mockReset();
  mockApi.testNotify.mockReset();
});
afterEach(() => cleanup());

describe('SettingsPage', () => {
  it('renders and fills the form from the server response', async () => {
    mockApi.settingsNotify.mockResolvedValue(okData());
    renderPage(<SettingsPage />);
    expect(await screen.findByLabelText('SMTP 主机')).toHaveValue('smtp.qq.com');
    expect(screen.getByText('设置 · 通知')).toBeInTheDocument();
    expect((screen.getByLabelText('发件地址') as HTMLInputElement).value).toBe('mc@x.com');
    expect(screen.getByText('凌晨登录')).toBeInTheDocument();
    // 密码已设置
    expect(screen.getByLabelText(/授权码\/密码（已设置）/)).toBeInTheDocument();
    // stats
    expect(screen.getByText('今日 QQ 推送')).toBeInTheDocument();
  });

  it('save with no password typed sends no password field; fresh form has no 保存', async () => {
    const data = okData();
    mockApi.settingsNotify.mockResolvedValue(data);
    mockApi.updateSettingsNotify.mockResolvedValue(data);
    renderPage(<SettingsPage />);
    await screen.findByLabelText('SMTP 主机');

    // No changes yet -> save disabled.
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();

    // Change the host to make it dirty.
    fireEvent.change(screen.getByLabelText('SMTP 主机'), { target: { value: 'smtp.example.com' } });
    const save = screen.getByRole('button', { name: '保存' });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() => expect(mockApi.updateSettingsNotify).toHaveBeenCalledTimes(1));
    const payload = mockApi.updateSettingsNotify.mock.calls[0][0];
    expect(payload.email.host).toBe('smtp.example.com');
    expect('password' in payload.email).toBe(false);
  });

  it('typed password is sent as a string in the payload', async () => {
    const data = okData();
    mockApi.settingsNotify.mockResolvedValue(data);
    mockApi.updateSettingsNotify.mockResolvedValue(data);
    renderPage(<SettingsPage />);
    await screen.findByLabelText('SMTP 主机');

    fireEvent.change(screen.getByLabelText(/授权码\/密码/), { target: { value: 'secret-authcode' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockApi.updateSettingsNotify).toHaveBeenCalledTimes(1));
    const payload = mockApi.updateSettingsNotify.mock.calls[0][0];
    expect(payload.email.password).toBe('secret-authcode');
  });

  it('rule enable toggle and severity override enter the payload', async () => {
    const data = okData();
    mockApi.settingsNotify.mockResolvedValue(data);
    mockApi.updateSettingsNotify.mockResolvedValue(data);
    renderPage(<SettingsPage />);
    await screen.findByLabelText('SMTP 主机');

    fireEvent.change(screen.getByLabelText('级别覆盖 凌晨登录'), { target: { value: 'critical' } });
    fireEvent.click(screen.getByLabelText('启用 疑似飞行'));

    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mockApi.updateSettingsNotify).toHaveBeenCalledTimes(1));
    const payload = mockApi.updateSettingsNotify.mock.calls[0][0];
    expect(payload.rules.night_login).toEqual({ enabled: true, severity: 'critical' });
    expect(payload.rules.flight_suspect.enabled).toBe(false);
  });

  it('auto-disabled rules show the reason and cannot be toggled', async () => {
    const data = okData();
    data.rules.push({
      id: 'fingerprint_mismatch',
      name: '客户端指纹不符',
      description: 'carpet 握手',
      default_severity: 'critical',
      enabled: true,
      severity: null,
      unavailable: 'EasyAuth 在玩家登录前丢弃 carpet 客户端握手',
    });
    mockApi.settingsNotify.mockResolvedValue(data);
    renderPage(<SettingsPage />);
    const box = await screen.findByLabelText('启用 客户端指纹不符');
    expect(box).toBeDisabled();
    expect(box).not.toBeChecked();
    expect(screen.getByText(/已自动停用：EasyAuth 在玩家登录前丢弃/)).toBeInTheDocument();
  });

  it('shows the 422 detail on save failure', async () => {
    mockApi.settingsNotify.mockResolvedValue(okData());
    mockApi.updateSettingsNotify.mockRejectedValue(new ApiError(422, '启用邮件时必须填写 SMTP 主机'));
    renderPage(<SettingsPage />);
    await screen.findByLabelText('SMTP 主机');

    fireEvent.change(screen.getByLabelText('SMTP 主机'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(await screen.findByText('启用邮件时必须填写 SMTP 主机')).toBeInTheDocument();
  });

  it('test button calls testNotify and shows the result', async () => {
    mockApi.settingsNotify.mockResolvedValue(okData());
    mockApi.testNotify.mockResolvedValue({ ok: true, detail: '已向 admin@x.com 发送测试邮件。' });
    renderPage(<SettingsPage />);
    await screen.findByLabelText('SMTP 主机');

    fireEvent.click(screen.getByRole('button', { name: '发送测试邮件' }));
    await waitFor(() => expect(mockApi.testNotify).toHaveBeenCalledWith('email'));
    expect(await screen.findByText('已向 admin@x.com 发送测试邮件。')).toBeInTheDocument();
  });

  it('disables the test button when there are unsaved changes', async () => {
    mockApi.settingsNotify.mockResolvedValue(okData());
    renderPage(<SettingsPage />);
    await screen.findByLabelText('SMTP 主机');

    expect(screen.getByRole('button', { name: '发送测试邮件' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('SMTP 主机'), { target: { value: 'smtp.example.com' } });
    expect(screen.getByRole('button', { name: '发送测试邮件' })).toBeDisabled();
    expect(screen.getAllByText('请先保存后再测试').length).toBeGreaterThan(0);
  });

  it('shows a warning when the bot is not connected', async () => {
    mockApi.settingsNotify.mockResolvedValue(
      okData({ bot: { enabled: false, connected: false } }),
    );
    renderPage(<SettingsPage />);
    await screen.findByLabelText('SMTP 主机');
    expect(screen.getByText(/机器人未启用/)).toBeInTheDocument();
  });

  it('selecting a known group as alert group enters the payload', async () => {
    const data = okData();
    mockApi.settingsNotify.mockResolvedValue(data);
    mockApi.updateSettingsNotify.mockResolvedValue(data);
    renderPage(<SettingsPage />);
    await screen.findByLabelText('SMTP 主机');

    fireEvent.click(screen.getByLabelText('告警群 OPEN..._BBB'));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mockApi.updateSettingsNotify).toHaveBeenCalledTimes(1));
    const payload = mockApi.updateSettingsNotify.mock.calls[0][0];
    const openids = payload.qq.groups.map((g: { openid: string }) => g.openid);
    expect(openids).toContain('OPENID_BBB');
    expect(openids).toContain('OPENID_AAA');
  });

  it('shows a friendly error when settings fail to load', async () => {
    mockApi.settingsNotify.mockRejectedValue(new ApiError(500, '无法加载通知设置'));
    renderPage(<SettingsPage />);
    expect(await screen.findByText('无法加载通知设置')).toBeInTheDocument();
  });
});
