import { apiFetch } from './http';

// ---- 类型 ----

export type ModKind = 'fabric' | 'unknown';

export interface ModInfo {
  file: string;
  enabled: boolean;
  size: number;
  mtime: string;
  sha1: string;
  sha512: string;
  id: string;
  name: string;
  version: string;
  description: string;
  authors: string[];
  homepage: string | null;
  depends: Record<string, string>;
  environment: string;
  provides: string[];
  kind: ModKind;
}

export interface HealthIssue {
  level: 'error' | 'warning';
  mod: string;
  message: string;
}

export interface ModUpdate {
  source: 'modrinth' | 'unknown';
  has_update?: boolean;
  project_id?: string | null;
  version_id?: string | null;
  version_number?: string | null;
  date_published?: string | null;
  changelog?: string;
  filename?: string | null;
  url?: string | null;
  sha512?: string | null;
  size?: number | null;
}

export interface PendingRestart {
  since: string;
  changes: { action: string; [k: string]: unknown }[];
}

export interface ModsOverview {
  mc_version: string | null;
  loader_version: string | null;
  loader_latest: string | null;
  mods: ModInfo[];
  issues: HealthIssue[];
  updates: Record<string, ModUpdate>;
  checked_at: string | null;
  update_error: string | null;
  pending_restart: PendingRestart | null;
}

export interface UpdateResult {
  file: string;
  ok: boolean;
  error?: string;
  new_file?: string;
  backup?: string;
}

export interface UpdateAllResponse {
  results: UpdateResult[];
}

export interface ModsApi {
  overview(signal?: AbortSignal): Promise<ModsOverview>;
  checkUpdates(): Promise<ModsOverview>;
  disable(file: string): Promise<ModInfo>;
  enable(file: string): Promise<ModInfo>;
  update(file: string): Promise<UpdateResult>;
  updateAll(): Promise<UpdateAllResponse>;
  rollback(file: string): Promise<{ restored: string; removed: string }>;
}

// ---- 真实实现 ----

const realModsApi: ModsApi = {
  overview(signal) {
    return apiFetch<ModsOverview>('/mods', signal ? { signal } : {});
  },
  checkUpdates() {
    return apiFetch<ModsOverview>('/mods/check-updates', { method: 'POST' });
  },
  disable(file) {
    return apiFetch<ModInfo>(`/mods/${encodeURIComponent(file)}/disable`, { method: 'POST' });
  },
  enable(file) {
    return apiFetch<ModInfo>(`/mods/${encodeURIComponent(file)}/enable`, { method: 'POST' });
  },
  update(file) {
    return apiFetch<UpdateResult>(`/mods/${encodeURIComponent(file)}/update`, { method: 'POST' });
  },
  updateAll() {
    return apiFetch<UpdateAllResponse>('/mods/update-all', { method: 'POST' });
  },
  rollback(file) {
    return apiFetch<{ restored: string; removed: string }>(
      `/mods/${encodeURIComponent(file)}/rollback`,
      { method: 'POST' },
    );
  },
};

// ---- mock 实现（VITE_MOCK=1 时用于前端独立开发） ----

function mkMod(partial: Partial<ModInfo> & Pick<ModInfo, 'file' | 'id' | 'name'>): ModInfo {
  return {
    enabled: true,
    size: 1024 * 512,
    mtime: '2026-10-01T12:00:00Z',
    sha1: 'a'.repeat(40),
    sha512: 'b'.repeat(128),
    version: '1.0.0',
    description: '示例 mod',
    authors: ['someone'],
    homepage: null,
    depends: { minecraft: '1.21.11', fabricloader: '>=0.16' },
    environment: '*',
    provides: [],
    kind: 'fabric',
    ...partial,
  };
}

const mockOverview: ModsOverview = {
  mc_version: '1.21.11',
  loader_version: '0.19.2',
  loader_latest: '0.20.0',
  mods: [
    mkMod({ file: 'fabric-api.jar', id: 'fabric-api', name: 'Fabric API', version: '0.100.0' }),
    mkMod({ file: 'easyauth.jar', id: 'easyauth', name: 'EasyAuth', version: '3.0.0' }),
    mkMod({
      file: 'lithium.jar',
      id: 'lithium',
      name: 'Lithium',
      version: '0.12.0',
      homepage: 'https://modrinth.com/mod/lithium',
    }),
    mkMod({
      file: 'oldmod.jar',
      id: 'oldmod',
      name: '旧版 Mod',
      version: '1.0.0',
      enabled: false,
    }),
  ],
  issues: [
    { level: 'error', mod: 'dup', message: '重复的 mod id「dup」：a.jar、b.jar' },
    {
      level: 'warning',
      mod: 'oldmod.jar',
      message: '旧版 Mod 要求 Minecraft 1.20.1，当前为 1.21.11，可能不兼容',
    },
  ],
  updates: {
    'lithium.jar': {
      source: 'modrinth',
      has_update: true,
      project_id: 'lithium',
      version_id: 'v2',
      version_number: '0.13.0',
      date_published: '2026-09-20T00:00:00Z',
      changelog: '性能优化若干',
      filename: 'lithium-0.13.0.jar',
      url: 'https://cdn.modrinth.com/lithium.jar',
      sha512: 'c'.repeat(128),
      size: 1024 * 600,
    },
    'fabric-api.jar': { source: 'modrinth', has_update: false },
    'easyauth.jar': { source: 'unknown' },
  },
  checked_at: '2026-10-09T08:00:00Z',
  update_error: null,
  pending_restart: null,
};

const mockModsApi: ModsApi = {
  async overview() {
    return structuredClone(mockOverview);
  },
  async checkUpdates() {
    return structuredClone(mockOverview);
  },
  async disable(file) {
    return mkMod({ file, id: file, name: file, enabled: false });
  },
  async enable(file) {
    return mkMod({ file, id: file, name: file, enabled: true });
  },
  async update(file) {
    return { file, ok: true, new_file: file, backup: `${file}.replaced-20261009080000` };
  },
  async updateAll() {
    return { results: [{ file: 'lithium.jar', ok: true, new_file: 'lithium-0.13.0.jar' }] };
  },
  async rollback(file) {
    return { restored: 'oldmod.jar', removed: file };
  },
};

export const modsApi: ModsApi = import.meta.env.VITE_MOCK === '1' ? mockModsApi : realModsApi;
