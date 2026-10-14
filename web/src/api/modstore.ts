// P2：Modrinth 搜索 / 一键安装（含依赖）/ 上传 jar / mrpack 整合包 / config 在线编辑。
// 后端见 src/mcops/modstore.py 与 api_mods.py。VITE_MOCK=1 时走内存 mock。

import { apiFetch } from './http';

export interface SearchHit {
  project_id: string;
  slug: string;
  title: string;
  description: string;
  author: string;
  downloads: number;
  icon_url: string | null;
  categories: string[];
  client_side: string | null;
  server_side: string | null;
  latest_version: string | null;
  date_modified: string | null;
  installed_file: string | null;
}

export interface SearchResult {
  hits: SearchHit[];
  total: number;
  offset: number;
  limit: number;
  mc_version: string | null;
}

export type SearchIndex = 'relevance' | 'downloads' | 'follows' | 'newest' | 'updated';

export interface ModVersion {
  version_id: string;
  project_id: string;
  version_number: string;
  name: string;
  version_type: 'release' | 'beta' | 'alpha' | string;
  date_published: string;
  game_versions: string[];
  loaders: string[];
  filename: string | null;
  size: number | null;
}

export type PlanStatus = 'install' | 'installed' | 'unavailable' | 'client_only';

export interface PlanItem {
  project_id: string;
  title: string;
  version_id: string;
  version_number: string;
  filename: string;
  size: number | null;
  reason: 'requested' | 'dependency';
  required_by: string[];
  status: PlanStatus;
}

export interface InstallPlan {
  mc_version: string;
  items: PlanItem[];
  install_count: number;
  download_bytes: number;
  warnings: string[];
}

export interface InstallRequest {
  project_id: string;
  version_id?: string | null;
}

export interface UploadResult {
  file: string;
  id: string;
  name: string;
  version: string;
  warnings: string[];
}

export interface MrpackPlan {
  token: string;
  name: string | null;
  version: string | null;
  summary: string | null;
  dependencies: Record<string, string>;
  files: { path: string; size: number | null }[];
  overrides: string[];
  skipped: { path: string; reason: string }[];
  warnings: string[];
  download_bytes: number;
}

export interface MrpackApplyResult {
  written: string[];
  replaced: string[];
  backup_dir: string | null;
}

export interface ConfigFileEntry {
  path: string;
  size: number;
  mtime: string;
  editable: boolean;
}

export interface ConfigFile {
  path: string;
  content: string;
  format: 'json' | 'toml' | 'yaml' | 'text';
  mtime: number;
}

export interface ModStoreApi {
  search(
    q: string,
    opts: { offset?: number; index?: SearchIndex; anyVersion?: boolean },
    signal?: AbortSignal,
  ): Promise<SearchResult>;
  versions(
    projectId: string,
    allVersions?: boolean,
  ): Promise<{ items: ModVersion[]; mc_version: string | null }>;
  plan(items: InstallRequest[]): Promise<InstallPlan>;
  install(items: InstallRequest[]): Promise<{ installed: string[]; plan: InstallPlan }>;
  upload(file: File): Promise<UploadResult>;
  mrpackPlan(file: File): Promise<MrpackPlan>;
  mrpackApply(token: string): Promise<MrpackApplyResult>;
  configFiles(): Promise<{ items: ConfigFileEntry[] }>;
  configFile(path: string): Promise<ConfigFile>;
  saveConfigFile(
    path: string,
    content: string,
    mtime: number | null,
  ): Promise<{ path: string; backup: string; mtime: number }>;
}

const real: ModStoreApi = {
  search(q, opts, signal) {
    const p = new URLSearchParams({
      q,
      offset: String(opts.offset ?? 0),
      index: opts.index ?? 'relevance',
    });
    if (opts.anyVersion) p.set('any_version', 'true');
    return apiFetch<SearchResult>(`/mods/search?${p.toString()}`, signal ? { signal } : {});
  },
  versions(projectId, allVersions) {
    const q = allVersions ? '?all_versions=true' : '';
    return apiFetch(`/mods/project/${encodeURIComponent(projectId)}/versions${q}`);
  },
  plan(items) {
    return apiFetch<InstallPlan>('/mods/install/plan', {
      method: 'POST',
      body: { items },
    });
  },
  install(items) {
    return apiFetch('/mods/install', { method: 'POST', body: { items } });
  },
  upload(file) {
    return apiFetch<UploadResult>(`/mods/upload?filename=${encodeURIComponent(file.name)}`, {
      method: 'POST',
      rawBody: file,
    });
  },
  mrpackPlan(file) {
    return apiFetch<MrpackPlan>('/mods/mrpack/plan', {
      method: 'POST',
      rawBody: file,
    });
  },
  mrpackApply(token) {
    return apiFetch<MrpackApplyResult>('/mods/mrpack/apply', {
      method: 'POST',
      body: { token },
    });
  },
  configFiles() {
    return apiFetch('/mods/config-files');
  },
  configFile(path) {
    return apiFetch<ConfigFile>(`/mods/config-file?path=${encodeURIComponent(path)}`);
  },
  saveConfigFile(path, content, mtime) {
    return apiFetch('/mods/config-file', {
      method: 'PUT',
      body: { path, content, mtime },
    });
  },
};

// ---- mock ----

const MOCK_HITS: SearchHit[] = [
  ['lithium', 'Lithium', '通用服务端性能优化，不改变游戏机制', 'jellysquid3', 23_000_000, 'optional'],
  ['ferrite-core', 'FerriteCore', '降低内存占用', 'malte0811', 18_000_000, 'optional'],
  ['chunky', 'Chunky', '预生成区块，减少玩家跑图时的卡顿', 'pop4959', 9_000_000, 'required'],
  ['spark', 'spark', '性能分析：找出卡服的实体/区块/mod', 'lucko', 11_000_000, 'optional'],
  ['sodium', 'Sodium', '客户端渲染优化（服务端不需要）', 'jellysquid3', 60_000_000, 'unsupported'],
].map(([slug, title, description, author, downloads, server_side]) => ({
  project_id: `P-${slug as string}`,
  slug: slug as string,
  title: title as string,
  description: description as string,
  author: author as string,
  downloads: downloads as number,
  icon_url: null,
  categories: ['optimization'],
  client_side: 'optional',
  server_side: server_side as string,
  latest_version: '1.21.11',
  date_modified: '2026-09-20T00:00:00Z',
  installed_file: slug === 'ferrite-core' ? 'ferritecore-7.0.0-fabric.jar' : null,
}));

const mockConfig: Record<string, ConfigFile> = {
  'lithium.properties': {
    path: 'lithium.properties',
    content: '# Lithium\nmixin.ai.pathing=true\n',
    format: 'text',
    mtime: 1,
  },
  'chunky/config.json': {
    path: 'chunky/config.json',
    content: '{\n  "version": 2,\n  "continueOnRestart": false\n}\n',
    format: 'json',
    mtime: 1,
  },
  'easyauth/main.conf': {
    path: 'easyauth/main.conf',
    content: 'premium-auto-login = false\n',
    format: 'text',
    mtime: 1,
  },
};

const delay = <T>(v: T) => new Promise<T>((r) => setTimeout(() => r(v), 150));

const mock: ModStoreApi = {
  async search(q) {
    const f = q.trim().toLowerCase();
    const hits = MOCK_HITS.filter((h) => !f || h.title.toLowerCase().includes(f) || h.slug.includes(f));
    return delay({
      hits,
      total: hits.length,
      offset: 0,
      limit: 20,
      mc_version: '1.21.11',
    });
  },
  async versions(projectId) {
    return delay({
      mc_version: '1.21.11',
      items: [
        {
          version_id: `${projectId}-v2`,
          project_id: projectId,
          version_number: '0.16.2',
          name: '0.16.2',
          version_type: 'release',
          date_published: '2026-09-20T00:00:00Z',
          game_versions: ['1.21.11'],
          loaders: ['fabric'],
          filename: 'mod-0.16.2.jar',
          size: 712_000,
        },
        {
          version_id: `${projectId}-v1`,
          project_id: projectId,
          version_number: '0.16.1-beta',
          name: '0.16.1',
          version_type: 'beta',
          date_published: '2026-08-01T00:00:00Z',
          game_versions: ['1.21.11'],
          loaders: ['fabric'],
          filename: 'mod-0.16.1.jar',
          size: 701_000,
        },
      ],
    });
  },
  async plan(items) {
    const hit = MOCK_HITS.find((h) => h.project_id === items[0]?.project_id);
    const title = hit?.title ?? items[0]?.project_id ?? '';
    if (hit?.server_side === 'unsupported') {
      return delay({
        mc_version: '1.21.11',
        install_count: 0,
        download_bytes: 0,
        warnings: [`「${title}」是纯客户端 mod，服务端不需要安装`],
        items: [
          {
            project_id: hit.project_id,
            title,
            version_id: '',
            version_number: '',
            filename: '',
            size: null,
            reason: 'requested',
            required_by: [],
            status: 'client_only',
          },
        ],
      });
    }
    return delay({
      mc_version: '1.21.11',
      install_count: 1,
      download_bytes: 712_000,
      warnings: [],
      items: [
        {
          project_id: items[0]?.project_id ?? '',
          title,
          version_id: 'v2',
          version_number: '0.16.2',
          filename: `${hit?.slug ?? 'mod'}-0.16.2.jar`,
          size: 712_000,
          reason: 'requested',
          required_by: [],
          status: 'install',
        },
        {
          project_id: 'P-fabric-api',
          title: 'Fabric API',
          version_id: '',
          version_number: '',
          filename: 'fabric-api-0.130.0.jar',
          size: null,
          reason: 'dependency',
          required_by: [title],
          status: 'installed',
        },
      ],
    });
  },
  async install(items) {
    const plan = await mock.plan(items);
    return delay({
      installed: plan.items.filter((i) => i.status === 'install').map((i) => i.filename),
      plan,
    });
  },
  async upload(file) {
    if (!file.name.endsWith('.jar')) throw new Error('非法的文件名');
    return delay({
      file: file.name,
      id: 'uploaded',
      name: file.name,
      version: '1.0.0',
      warnings: [],
    });
  },
  async mrpackPlan(file) {
    return delay({
      token: 'mock-token',
      name: file.name.replace(/\.mrpack$/, ''),
      version: '1.0',
      summary: '示例整合包',
      dependencies: { minecraft: '1.21.11', 'fabric-loader': '0.19.2' },
      files: [
        { path: 'mods/lithium.jar', size: 712_000 },
        { path: 'mods/chunky.jar', size: 400_000 },
      ],
      overrides: ['overrides/config/lithium.properties'],
      skipped: [
        { path: 'mods/sodium.jar', reason: '仅客户端' },
        { path: 'resourcepacks/x.zip', reason: '服务端不需要的目录' },
      ],
      warnings: [],
      download_bytes: 1_112_000,
    });
  },
  async mrpackApply() {
    return delay({
      written: ['mods/lithium.jar', 'mods/chunky.jar', 'config/lithium.properties'],
      replaced: [],
      backup_dir: null,
    });
  },
  async configFiles() {
    return delay({
      items: Object.values(mockConfig).map((f) => ({
        path: f.path,
        size: f.content.length,
        mtime: '2026-10-10T00:00:00Z',
        editable: true,
      })),
    });
  },
  async configFile(path) {
    const f = mockConfig[path];
    if (!f) throw new Error('文件不存在');
    return delay({ ...f });
  },
  async saveConfigFile(path, content) {
    const f = mockConfig[path];
    if (!f) throw new Error('文件不存在');
    if (f.format === 'json') JSON.parse(content);
    f.content = content;
    f.mtime += 1;
    return delay({
      path,
      backup: `.mcops-backup/config/${path}.bak`,
      mtime: f.mtime,
    });
  },
};

export const modStoreApi: ModStoreApi = import.meta.env.VITE_MOCK === '1' ? mock : real;
