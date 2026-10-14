// P1: 配置入库 + 面板编辑。见 docs/03-config-api.md。
// 独立 api 模块（与 mods.ts / network.ts 一致），避免把巨大的 schema 类型塞进
// 全局 types.ts / client.ts。mock 实现见 configMock.ts，由 index.ts 按 IS_MOCK 切换。
import { apiFetch } from './http';

// ---- JSON Schema（pydantic model_json_schema 产物的子集） --------------------

/** 生效方式：agent 重启生效 / mc 下次开服生效 / live 立即生效。 */
export type ApplyKind = 'agent' | 'mc' | 'live';

/** 自定义 widget：memory 内存滑块、time HH:MM、lines 字符串数组按行编辑。 */
export type WidgetKind = 'memory' | 'time' | 'lines';

/**
 * 一个 schema 节点。只覆盖本项目生成的形态：对象、字符串、整数/浮点、布尔、
 * 枚举（Literal/enum）、字符串数组、可空字段（anyOf [T, null]）、三元 int 元组（spawn）。
 */
export interface SchemaNode {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  properties?: Record<string, SchemaNode>;
  required?: string[];
  items?: SchemaNode | SchemaNode[];
  prefixItems?: SchemaNode[]; // 元组（spawn: [int,int,int]）
  minItems?: number;
  maxItems?: number;
  enum?: unknown[];
  const?: unknown;
  anyOf?: SchemaNode[];
  allOf?: SchemaNode[];
  oneOf?: SchemaNode[];
  $ref?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  // 扩展字段
  'x-apply'?: ApplyKind;
  'x-advanced'?: boolean;
  'x-unit'?: string;
  'x-widget'?: WidgetKind;
  [k: string]: unknown;
}

export interface RootSchema extends SchemaNode {
  $defs?: Record<string, SchemaNode>;
}

export interface ConfigSection {
  key: string;
  title: string;
  description?: string;
}

export interface PendingItem {
  path: string;
  apply: ApplyKind;
}

export interface ConfigReadonly {
  mode?: string;
  data_dir?: string;
  api?: string;
  config_file?: string;
  [k: string]: unknown;
}

export type ConfigValues = Record<string, unknown>;

export interface ConfigResponse {
  schema: RootSchema;
  sections: ConfigSection[];
  values: ConfigValues;
  overridden: string[];
  pending: PendingItem[];
  readonly: ConfigReadonly;
}

export interface ConfigUpdateResult {
  ok: true;
  changed: PendingItem[];
  pending: PendingItem[];
  values: ConfigValues;
  overridden: string[];
}

export interface ConfigHistoryItem {
  id: number;
  ts: string;
  user: string;
  changed: string[];
  note: string;
}

export interface ConfigHistoryResponse {
  items: ConfigHistoryItem[];
}

// ---- server.properties ------------------------------------------------------

export type PropType = 'bool' | 'int' | 'enum' | 'string';

export interface PropItem {
  key: string;
  value: string | number | boolean | null;
  known: boolean;
  type: PropType;
  options?: string[];
  min?: number;
  max?: number;
  group: string;
  title?: string;
  description?: string;
  managed: boolean;
  managed_reason?: string;
  sensitive?: boolean;
}

export interface PropertiesResponse {
  path: string;
  items: PropItem[];
  pending: string[];
}

export interface PropertiesUpdateResult {
  ok: true;
  changed: string[];
  backup: string;
}

// ---- 账号与机密 -------------------------------------------------------------

export interface TotpStatus {
  enabled: boolean;
}

export interface TotpSetupResponse {
  secret: string;
  otpauth_uri: string;
}

export interface SecretItem {
  key: string;
  title: string;
  set: boolean;
  editable: boolean;
  apply?: ApplyKind;
  note?: string;
}

export interface SecretsResponse {
  items: SecretItem[];
}

export interface SecretsUpdateResult {
  ok: true;
  pending: PendingItem[];
}

// ---- capabilities -----------------------------------------------------------

export interface Capabilities {
  mc_version: string;
  loader: string;
  loader_version: string;
  mods: {
    easyauth: boolean;
    carpet: boolean;
    proxy_protocol: boolean;
    minimotd: boolean;
    [k: string]: boolean;
  };
  proxy_source: string | null;
  features: {
    edge: boolean;
    bot: boolean;
    restic: boolean;
    systemd: boolean;
    flight_watch: boolean;
    [k: string]: boolean;
  };
}

export interface RestartAgentResult {
  accepted: true;
}

// ---- 真实 client ------------------------------------------------------------
// index.ts 负责按 VITE_MOCK 在此与 configMockApi 之间切换并对外暴露。

const realConfigApi = {
  config(signal?: AbortSignal): Promise<ConfigResponse> {
    return apiFetch<ConfigResponse>('/config', signal ? { signal } : {});
  },
  updateConfig(values: ConfigValues, note?: string): Promise<ConfigUpdateResult> {
    return apiFetch<ConfigUpdateResult>('/config', {
      method: 'PUT',
      body: note !== undefined && note !== '' ? { values, note } : { values },
    });
  },
  resetConfig(paths: string[]): Promise<ConfigUpdateResult> {
    return apiFetch<ConfigUpdateResult>('/config/reset', {
      method: 'POST',
      body: { paths },
    });
  },
  history(limit = 50, signal?: AbortSignal): Promise<ConfigHistoryResponse> {
    return apiFetch<ConfigHistoryResponse>(`/config/history?limit=${limit}`, signal ? { signal } : {});
  },
  restoreHistory(id: number): Promise<ConfigUpdateResult> {
    return apiFetch<ConfigUpdateResult>(`/config/history/${id}/restore`, { method: 'POST' });
  },
  restartAgent(): Promise<RestartAgentResult> {
    return apiFetch<RestartAgentResult>('/system/restart-agent', { method: 'POST' });
  },
  capabilities(signal?: AbortSignal): Promise<Capabilities> {
    return apiFetch<Capabilities>('/capabilities', signal ? { signal } : {});
  },

  // server.properties
  properties(signal?: AbortSignal): Promise<PropertiesResponse> {
    return apiFetch<PropertiesResponse>('/gamecfg/properties', signal ? { signal } : {});
  },
  updateProperties(changes: Record<string, string | number | boolean>): Promise<PropertiesUpdateResult> {
    return apiFetch<PropertiesUpdateResult>('/gamecfg/properties', {
      method: 'PUT',
      body: { changes },
    });
  },

  // 账号与机密
  changePassword(oldPassword: string, newPassword: string): Promise<void> {
    return apiFetch<void>('/auth/password', {
      method: 'POST',
      body: { old_password: oldPassword, new_password: newPassword },
    });
  },
  totpStatus(signal?: AbortSignal): Promise<TotpStatus> {
    return apiFetch<TotpStatus>('/auth/totp', signal ? { signal } : {});
  },
  totpSetup(): Promise<TotpSetupResponse> {
    return apiFetch<TotpSetupResponse>('/auth/totp/setup', { method: 'POST' });
  },
  totpConfirm(code: string): Promise<TotpStatus> {
    return apiFetch<TotpStatus>('/auth/totp/confirm', { method: 'POST', body: { code } });
  },
  totpDisable(password: string): Promise<TotpStatus> {
    return apiFetch<TotpStatus>('/auth/totp/disable', { method: 'POST', body: { password } });
  },
  secrets(signal?: AbortSignal): Promise<SecretsResponse> {
    return apiFetch<SecretsResponse>('/secrets', signal ? { signal } : {});
  },
  updateSecrets(values: Record<string, string>): Promise<SecretsUpdateResult> {
    return apiFetch<SecretsUpdateResult>('/secrets', { method: 'PUT', body: values });
  },
};

export type ConfigApi = typeof realConfigApi;

// ---- mock 适配：把 configMock.ts 的自由函数组装成同形对象 --------------------
import {
  mockCapabilities,
  mockChangePassword,
  mockConfig,
  mockHistory,
  mockProperties,
  mockResetConfig,
  mockRestartAgent,
  mockRestoreHistory,
  mockSecrets,
  mockTotpConfirm,
  mockTotpDisable,
  mockTotpSetup,
  mockTotpStatus,
  mockUpdateConfig,
  mockUpdateProperties,
  mockUpdateSecrets,
} from './configMock';

const delay = (ms = 120) => new Promise<void>((r) => setTimeout(r, ms));

const mockConfigApi: ConfigApi = {
  async config() {
    await delay(90);
    return mockConfig();
  },
  async updateConfig(values, note) {
    await delay(140);
    return mockUpdateConfig(values, note);
  },
  async resetConfig(paths) {
    await delay(120);
    return mockResetConfig(paths);
  },
  async history(limit = 50) {
    await delay(80);
    return mockHistory(limit);
  },
  async restoreHistory(id) {
    await delay(140);
    return mockRestoreHistory(id);
  },
  async restartAgent() {
    await delay(200);
    return mockRestartAgent();
  },
  async capabilities() {
    await delay(60);
    return mockCapabilities();
  },
  async properties() {
    await delay(90);
    return mockProperties();
  },
  async updateProperties(changes) {
    await delay(140);
    return mockUpdateProperties(changes);
  },
  async changePassword(oldPassword, newPassword) {
    await delay(160);
    mockChangePassword(oldPassword, newPassword);
  },
  async totpStatus() {
    await delay(60);
    return mockTotpStatus();
  },
  async totpSetup() {
    await delay(120);
    return mockTotpSetup();
  },
  async totpConfirm(code) {
    await delay(120);
    return mockTotpConfirm(code);
  },
  async totpDisable(password) {
    await delay(120);
    return mockTotpDisable(password);
  },
  async secrets() {
    await delay(80);
    return mockSecrets();
  },
  async updateSecrets(values) {
    await delay(140);
    return mockUpdateSecrets(values);
  },
};

/** 对外暴露：mock 模式用内存实现，否则走真实 fetch。 */
export const configApi: ConfigApi =
  import.meta.env.VITE_MOCK === '1' ? mockConfigApi : realConfigApi;
