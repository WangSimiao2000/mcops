// P3：游戏版本安装 / 升级与 Java。后端见 src/mcops/gameversions.py、api_game.py。

import { apiFetch } from './http';

export interface GameTarget {
  mc: string;
  loader: string;
  installer?: string | null;
}

export interface GameVersions {
  current: {
    jar: string | null;
    mc: string | null;
    loader: string | null;
    java_bin: string;
    java_major: number | null;
  };
  games: { version: string; stable: boolean }[];
  loaders: { version: string; stable: boolean }[];
  installer: string | null;
  managed_javas: { major: number; release: string; java_bin: string }[];
  prepared: PreparedInfo | null;
  prepare_error: string | null;
  unit_managed: boolean;
}

export interface PreparedInfo {
  target: { mc: string; loader: string; installer: string };
  jar: string;
  java_bin: string;
  mod_updates: { file: string; new_file: string }[];
  disable: string[];
  prewarm_log_tail: string;
  prepared_at: string;
}

export type ModCompat = 'update' | 'compatible' | 'unknown';

export interface Precheck {
  current: {
    jar: string | null;
    mc: string | null;
    loader: string | null;
    java_major: number | null;
    java_bin: string;
  };
  target: { mc: string; loader: string; installer: string; jar: string; java_major: number };
  java: { required: number; current: number | null; needs_install: boolean };
  mods: {
    file: string;
    id: string;
    name: string;
    version: string;
    status: ModCompat;
    new_version: string | null;
    new_file: string | null;
  }[];
  blockers: string[];
  warnings: string[];
  fresh_install: boolean;
}

export interface GameApi {
  versions(snapshots?: boolean): Promise<GameVersions>;
  precheck(t: GameTarget): Promise<Precheck>;
  prepare(
    t: GameTarget & { disable_unknown: boolean; java_mirror: 'tuna' | 'official' },
  ): Promise<{ accepted: boolean }>;
  upgrade(t: GameTarget): Promise<{ accepted: boolean }>;
}

const real: GameApi = {
  versions(snapshots) {
    return apiFetch<GameVersions>(`/game/versions${snapshots ? '?snapshots=true' : ''}`);
  },
  precheck(t) {
    return apiFetch<Precheck>('/game/precheck', { method: 'POST', body: t });
  },
  prepare(t) {
    return apiFetch('/game/prepare', { method: 'POST', body: t });
  },
  upgrade(t) {
    return apiFetch('/game/upgrade', { method: 'POST', body: t });
  },
};

// ---- mock ----

let mockPrepared: PreparedInfo | null = null;
const delay = <T>(v: T, ms = 200) => new Promise<T>((r) => setTimeout(() => r(v), ms));

const mock: GameApi = {
  async versions() {
    return delay({
      current: {
        jar: 'fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar',
        mc: '1.21.11',
        loader: '0.19.2',
        java_bin: '/usr/bin/java',
        java_major: 21,
      },
      games: ['26.3', '26.2', '26.1', '1.21.11', '1.21.10'].map((version) => ({ version, stable: true })),
      loaders: ['0.19.3', '0.19.2'].map((version) => ({ version, stable: true })),
      installer: '1.1.0',
      managed_javas: [],
      prepared: mockPrepared,
      prepare_error: null,
      unit_managed: true,
    });
  },
  async precheck(t) {
    const needsJava = t.mc.startsWith('26.');
    return delay({
      current: {
        jar: 'fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar',
        mc: '1.21.11',
        loader: '0.19.2',
        java_major: 21,
        java_bin: '/usr/bin/java',
      },
      target: {
        mc: t.mc,
        loader: t.loader,
        installer: '1.1.0',
        jar: `fabric-server-mc.${t.mc}-loader.${t.loader}-launcher.1.1.0.jar`,
        java_major: needsJava ? 25 : 21,
      },
      java: { required: needsJava ? 25 : 21, current: 21, needs_install: needsJava },
      mods: [
        {
          file: 'lithium-0.16.jar',
          id: 'lithium',
          name: 'Lithium',
          version: '0.16.2',
          status: 'update',
          new_version: '0.17.0',
          new_file: 'lithium-0.17.0.jar',
        },
        {
          file: 'fabric-api.jar',
          id: 'fabric-api',
          name: 'Fabric API',
          version: '0.141.6',
          status: 'update',
          new_version: '0.150.0',
          new_file: 'fabric-api-0.150.0.jar',
        },
        {
          file: 'easyauth.jar',
          id: 'easyauth',
          name: 'EasyAuth',
          version: '3.4.4',
          status: 'compatible',
          new_version: null,
          new_file: null,
        },
        {
          file: 'private-mod.jar',
          id: 'private',
          name: '自制 mod',
          version: '1.0',
          status: 'unknown',
          new_version: null,
          new_file: null,
        },
      ],
      blockers: [],
      warnings: [
        '1 个 mod 在 Modrinth 上找不到 ' +
          t.mc +
          ' 的版本（或不是从 Modrinth 装的），可以选择升级时先停用它们',
      ],
      fresh_install: false,
    });
  },
  async prepare(t) {
    setTimeout(() => {
      mockPrepared = {
        target: { mc: t.mc, loader: t.loader, installer: '1.1.0' },
        jar: `fabric-server-mc.${t.mc}.jar`,
        java_bin: '/usr/bin/java',
        mod_updates: [{ file: 'lithium-0.16.jar', new_file: 'lithium-0.17.0.jar' }],
        disable: t.disable_unknown ? ['private-mod.jar'] : [],
        prewarm_log_tail: 'You need to agree to the EULA in order to run the server.',
        prepared_at: new Date().toISOString(),
      };
    }, 1500);
    return delay({ accepted: true });
  },
  async upgrade() {
    mockPrepared = null;
    return delay({ accepted: true });
  },
};

export const gameApi: GameApi = import.meta.env.VITE_MOCK === '1' ? mock : real;
