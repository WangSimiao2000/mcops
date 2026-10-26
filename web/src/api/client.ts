import { apiFetch } from './http';
import { configApi } from './config';
import type {
  AcceptedResponse,
  AckAllResponse,
  AlertsResponse,
  AlertStatus,
  ApplicationsResponse,
  GrantsResponse,
  BackupsResponse,
  ResticInfo,
  ResticRepoId,
  RestoreScope,
  BotReplyResponse,
  BotStatus,
  CommandResponse,
  EdgeStatus,
  EventsResponse,
  HealthResponse,
  InteractionsResponse,
  IpInfo,
  JobRunAccepted,
  MapStatus,
  JobsResponse,
  MeResponse,
  NotifyChannel,
  NotifySettings,
  NotifySettingsUpdate,
  NotifyTestResult,
  PlayerDetailResponse,
  PlayersResponse,
  RulesResponse,
  ServerStatus,
  SessionsResponse,
  SimulateResponse,
  SystemResponse,
  WatchdogResetResponse,
  WhitelistResponse,
} from './types';

export const api = {
  // --- auth ---
  login(password: string, totp?: string): Promise<MeResponse> {
    const body: { password: string; totp?: string } =
      totp !== undefined && totp !== '' ? { password, totp } : { password };
    return apiFetch<MeResponse>('/auth/login', {
      method: 'POST',
      body,
      suppressUnauthorized: true, // login 401 is handled inline, not a redirect
    });
  },

  logout(): Promise<void> {
    return apiFetch<void>('/auth/logout', { method: 'POST' });
  },

  me(signal?: AbortSignal): Promise<MeResponse> {
    return apiFetch<MeResponse>('/auth/me', {
      suppressUnauthorized: true, // the auth bootstrap decides what to do with 401
      ...(signal ? { signal } : {}),
    });
  },

  // --- health / system ---
  health(signal?: AbortSignal): Promise<HealthResponse> {
    return apiFetch<HealthResponse>('/health', {
      suppressUnauthorized: true,
      ...(signal ? { signal } : {}),
    });
  },

  system(signal?: AbortSignal): Promise<SystemResponse> {
    return apiFetch<SystemResponse>('/system', signal ? { signal } : {});
  },

  // --- server ---
  serverStatus(signal?: AbortSignal): Promise<ServerStatus> {
    return apiFetch<ServerStatus>('/server/status', signal ? { signal } : {});
  },

  serverStart(): Promise<AcceptedResponse> {
    return apiFetch<AcceptedResponse>('/server/start', { method: 'POST' });
  },

  serverStop(): Promise<AcceptedResponse> {
    return apiFetch<AcceptedResponse>('/server/stop', { method: 'POST' });
  },

  serverRestart(): Promise<AcceptedResponse> {
    return apiFetch<AcceptedResponse>('/server/restart', { method: 'POST' });
  },

  sendCommand(command: string): Promise<CommandResponse> {
    return apiFetch<CommandResponse>('/server/command', {
      method: 'POST',
      body: { command },
    });
  },

  // --- events ---
  events(params?: { limit?: number; types?: string[] }, signal?: AbortSignal): Promise<EventsResponse> {
    const search = new URLSearchParams();
    if (params?.limit !== undefined) search.set('limit', String(params.limit));
    if (params?.types && params.types.length > 0) {
      search.set('types', params.types.join(','));
    }
    const qs = search.toString();
    return apiFetch<EventsResponse>(`/events${qs ? `?${qs}` : ''}`, signal ? { signal } : {});
  },

  // --- M1: players ---
  players(params?: { q?: string; limit?: number }, signal?: AbortSignal): Promise<PlayersResponse> {
    const search = new URLSearchParams();
    if (params?.q) search.set('q', params.q);
    search.set('limit', String(params?.limit ?? 200));
    const qs = search.toString();
    return apiFetch<PlayersResponse>(`/players${qs ? `?${qs}` : ''}`, signal ? { signal } : {});
  },

  player(name: string, signal?: AbortSignal): Promise<PlayerDetailResponse> {
    return apiFetch<PlayerDetailResponse>(
      `/players/${encodeURIComponent(name)}`,
      signal ? { signal } : {},
    );
  },

  // --- M1: sessions ---
  sessions(params?: { limit?: number; online?: boolean }, signal?: AbortSignal): Promise<SessionsResponse> {
    const search = new URLSearchParams();
    search.set('limit', String(params?.limit ?? 100));
    if (params?.online !== undefined) search.set('online', String(params.online));
    const qs = search.toString();
    return apiFetch<SessionsResponse>(`/sessions${qs ? `?${qs}` : ''}`, signal ? { signal } : {});
  },

  // --- M1: IP profile ---
  ip(ip: string, signal?: AbortSignal): Promise<IpInfo> {
    return apiFetch<IpInfo>(`/ips/${encodeURIComponent(ip)}`, signal ? { signal } : {});
  },

  // --- M1: security alerts / rules / edge ---
  alerts(params?: { status?: AlertStatus | 'all'; limit?: number }, signal?: AbortSignal): Promise<AlertsResponse> {
    const search = new URLSearchParams();
    if (params?.status) search.set('status', params.status);
    search.set('limit', String(params?.limit ?? 100));
    const qs = search.toString();
    return apiFetch<AlertsResponse>(`/alerts${qs ? `?${qs}` : ''}`, signal ? { signal } : {});
  },

  ackAlert(id: string): Promise<{ ok: true }> {
    return apiFetch<{ ok: true }>(`/alerts/${encodeURIComponent(id)}/ack`, { method: 'POST' });
  },

  ackAllAlerts(severity?: string): Promise<AckAllResponse> {
    return apiFetch<AckAllResponse>('/alerts/ack-all', {
      method: 'POST',
      body: severity ? { severity } : {},
    });
  },

  securityRules(signal?: AbortSignal): Promise<RulesResponse> {
    return apiFetch<RulesResponse>('/security/rules', signal ? { signal } : {});
  },

  edgeStatus(signal?: AbortSignal): Promise<EdgeStatus> {
    return apiFetch<EdgeStatus>('/edge/status', signal ? { signal } : {});
  },

  // --- M2: QQ bot ---
  botStatus(signal?: AbortSignal): Promise<BotStatus> {
    return apiFetch<BotStatus>('/bot/status', signal ? { signal } : {});
  },

  botApplications(signal?: AbortSignal): Promise<ApplicationsResponse> {
    return apiFetch<ApplicationsResponse>('/bot/applications', signal ? { signal } : {});
  },

  botGrants(signal?: AbortSignal): Promise<GrantsResponse> {
    return apiFetch<GrantsResponse>('/bot/grants', signal ? { signal } : {});
  },

  botApproveApplication(name: string): Promise<BotReplyResponse> {
    return apiFetch<BotReplyResponse>(
      `/bot/applications/${encodeURIComponent(name)}/approve`,
      { method: 'POST' },
    );
  },

  botRejectApplication(name: string): Promise<BotReplyResponse> {
    return apiFetch<BotReplyResponse>(
      `/bot/applications/${encodeURIComponent(name)}/reject`,
      { method: 'POST' },
    );
  },

  botInteractions(params?: { limit?: number; q?: string }, signal?: AbortSignal): Promise<InteractionsResponse> {
    const search = new URLSearchParams();
    search.set('limit', String(params?.limit ?? 100));
    if (params?.q) search.set('q', params.q);
    const qs = search.toString();
    return apiFetch<InteractionsResponse>(`/bot/interactions${qs ? `?${qs}` : ''}`, signal ? { signal } : {});
  },

  botSimulate(content: string, asAdmin: boolean): Promise<SimulateResponse> {
    return apiFetch<SimulateResponse>('/bot/simulate', {
      method: 'POST',
      body: { content, as_admin: asAdmin },
    });
  },

  // --- M2: whitelist ---
  whitelist(signal?: AbortSignal): Promise<WhitelistResponse> {
    return apiFetch<WhitelistResponse>('/whitelist', signal ? { signal } : {});
  },

  whitelistAdd(name: string): Promise<BotReplyResponse> {
    return apiFetch<BotReplyResponse>('/whitelist', {
      method: 'POST',
      body: { name },
    });
  },

  whitelistRemove(name: string): Promise<BotReplyResponse> {
    return apiFetch<BotReplyResponse>(`/whitelist/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
  },

  // --- M3: 运维任务 / 备份 ---
  jobs(signal?: AbortSignal): Promise<JobsResponse> {
    return apiFetch<JobsResponse>('/jobs', signal ? { signal } : {});
  },

  runJob(job: string): Promise<JobRunAccepted> {
    return apiFetch<JobRunAccepted>(`/jobs/${encodeURIComponent(job)}/run`, {
      method: 'POST',
    });
  },

  mapStatus(signal?: AbortSignal): Promise<MapStatus> {
    return apiFetch<MapStatus>('/map', signal ? { signal } : {});
  },

  runMap(): Promise<JobRunAccepted> {
    return apiFetch<JobRunAccepted>('/map/run', { method: 'POST' });
  },

  watchdogReset(): Promise<WatchdogResetResponse> {
    return apiFetch<WatchdogResetResponse>('/watchdog/reset', { method: 'POST' });
  },

  backups(signal?: AbortSignal): Promise<BackupsResponse> {
    return apiFetch<BackupsResponse>('/backups', signal ? { signal } : {});
  },

  restic(repo: ResticRepoId = 'local', signal?: AbortSignal): Promise<ResticInfo> {
    const path = repo === 'local' ? '/restic' : `/restic?repo=${encodeURIComponent(repo)}`;
    return apiFetch<ResticInfo>(path, signal ? { signal } : {});
  },

  restoreSnapshot(id: string, repo: ResticRepoId, scope: RestoreScope): Promise<JobRunAccepted> {
    return apiFetch<JobRunAccepted>(`/restic/${encodeURIComponent(id)}/restore`, {
      method: 'POST',
      body: { repo, scope },
    });
  },

  // --- M4: 通知设置 ---
  settingsNotify(signal?: AbortSignal): Promise<NotifySettings> {
    return apiFetch<NotifySettings>('/settings/notify', signal ? { signal } : {});
  },

  updateSettingsNotify(body: NotifySettingsUpdate): Promise<NotifySettings> {
    return apiFetch<NotifySettings>('/settings/notify', {
      method: 'PUT',
      body,
    });
  },

  testNotify(channel: NotifyChannel): Promise<NotifyTestResult> {
    return apiFetch<NotifyTestResult>('/settings/notify/test', {
      method: 'POST',
      body: { channel },
    });
  },

  // --- P1: 配置 / server.properties / 账号与机密 / capabilities ---
  // Delegated to configApi (own module to keep the big schema types out of here).
  config: configApi.config,
  updateConfig: configApi.updateConfig,
  resetConfig: configApi.resetConfig,
  configHistory: configApi.history,
  restoreConfigHistory: configApi.restoreHistory,
  restartAgent: configApi.restartAgent,
  capabilities: configApi.capabilities,
  properties: configApi.properties,
  updateProperties: configApi.updateProperties,
  changePassword: configApi.changePassword,
  totpStatus: configApi.totpStatus,
  totpSetup: configApi.totpSetup,
  totpConfirm: configApi.totpConfirm,
  totpDisable: configApi.totpDisable,
  secrets: configApi.secrets,
  updateSecrets: configApi.updateSecrets,
};
