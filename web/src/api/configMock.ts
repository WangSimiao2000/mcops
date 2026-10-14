// In-memory mock backend for the P1 配置 endpoints (see docs/03-config-api.md).
// Kept apart from the big mockApi.ts so the schema fixture stays readable; wired
// into the mock surface from mockApi.ts.
import { ApiError } from './http';
import type {
  Capabilities,
  ConfigHistoryResponse,
  ConfigResponse,
  ConfigUpdateResult,
  PropertiesResponse,
  PropertiesUpdateResult,
  RootSchema,
  SecretsResponse,
  SecretsUpdateResult,
  TotpSetupResponse,
  TotpStatus,
} from './config';

// ---- JSON Schema fixture (shaped like pydantic model_json_schema output) ----
// $defs + $ref, x-apply/x-advanced/x-unit/x-widget extensions, nested objects,
// enum (Literal), string arrays (lines), int tuple (spawn), nullable fields.

const schema: RootSchema = {
  type: 'object',
  $defs: {
    ResticConfig: {
      type: 'object',
      title: '异地增量备份（restic）',
      description: '每小时增量快照，加密去重。',
      properties: {
        enabled: { type: 'boolean', title: '启用 restic', 'x-apply': 'agent' },
        interval_minutes: {
          type: 'integer',
          title: '快照间隔',
          description: '相邻两次快照的间隔。',
          minimum: 15,
          maximum: 1440,
          'x-unit': '分钟',
          'x-apply': 'agent',
        },
        keep_hourly: { type: 'integer', title: '保留小时快照数', minimum: 0, maximum: 168, 'x-apply': 'agent' },
        remote: {
          anyOf: [{ type: 'string' }, { type: 'null' }],
          title: '异地副本地址',
          description: '形如 sftp:host:/path；留空表示仅本地仓库。',
          'x-advanced': true,
          'x-apply': 'agent',
        },
      },
      required: ['enabled', 'interval_minutes', 'keep_hourly'],
    },
  },
  properties: {
    server: {
      type: 'object',
      title: '服务端',
      properties: {
        java_opts: {
          type: 'array',
          items: { type: 'string' },
          title: 'JVM 参数',
          description: '内存滑块编辑 -Xms/-Xmx，其它参数保持不变。',
          'x-widget': 'memory',
          'x-apply': 'mc',
        },
        port: {
          type: 'integer',
          title: '游戏端口',
          description: 'server-port，同时写入 server.properties。',
          minimum: 1,
          maximum: 65535,
          'x-apply': 'mc',
        },
        motd: {
          type: 'string',
          title: 'MOTD',
          description: '服务器列表里显示的描述，开服前同步到 server.properties。',
          'x-apply': 'mc',
        },
        spawn: {
          prefixItems: [
            { type: 'integer' },
            { type: 'integer' },
            { type: 'integer' },
          ],
          minItems: 3,
          maxItems: 3,
          title: '世界出生点',
          description: '就绪后 setworldspawn 的 X / Y / Z 坐标。',
          'x-apply': 'mc',
        },
        allow_flight: {
          type: 'boolean',
          title: '允许飞行',
          description: '写入 server.properties 的 allow-flight。',
          'x-apply': 'mc',
        },
      },
      required: ['java_opts', 'port'],
    },
    ops: {
      type: 'object',
      title: '运维',
      properties: {
        restart_time: {
          type: 'string',
          title: '每日重启时间',
          description: '每天定时重启 + 冷备份的时刻（服务器本地时区）。',
          'x-widget': 'time',
          'x-apply': 'agent',
        },
        keep_backups: {
          type: 'integer',
          title: '保留冷备份数',
          minimum: 1,
          maximum: 60,
          'x-unit': '份',
          'x-apply': 'agent',
        },
        warn_minutes: {
          type: 'integer',
          title: '重启前预警',
          description: '重启前向在线玩家预告的分钟数。',
          minimum: 0,
          maximum: 30,
          'x-unit': '分钟',
          'x-advanced': true,
          'x-apply': 'agent',
        },
        restic: { $ref: '#/$defs/ResticConfig' },
      },
      required: ['restart_time', 'keep_backups'],
    },
    security: {
      type: 'object',
      title: '安全',
      properties: {
        night_start_hour: {
          type: 'integer',
          title: '凌晨时段起点',
          description: '夜间登录告警时段的起始小时（0–23）。',
          minimum: 0,
          maximum: 23,
          'x-unit': '点',
          'x-apply': 'live',
        },
        night_end_hour: {
          type: 'integer',
          title: '凌晨时段终点',
          minimum: 0,
          maximum: 23,
          'x-unit': '点',
          'x-apply': 'live',
        },
        auto_ban_scanners: {
          type: 'boolean',
          title: '自动封禁扫描器',
          description: '检测到端口/密码扫描特征时自动 ban-ip。',
          'x-apply': 'live',
        },
        brute_force_threshold: {
          type: 'integer',
          title: '爆破失败阈值',
          description: '同一 IP 连续失败多少次判定为爆破。',
          minimum: 3,
          maximum: 100,
          'x-unit': '次',
          'x-advanced': true,
          'x-apply': 'live',
        },
      },
      required: ['night_start_hour', 'night_end_hour'],
    },
    bot: {
      type: 'object',
      title: 'QQ 机器人',
      properties: {
        enabled: { type: 'boolean', title: '启用机器人', 'x-apply': 'agent' },
        admin_openids: {
          type: 'array',
          items: { type: 'string' },
          title: '管理员 openid',
          description: '每行一个，管理指令只接受来自这些成员的调用。',
          'x-widget': 'lines',
          'x-apply': 'agent',
        },
        alert_groups: {
          type: 'array',
          items: { type: 'string' },
          title: '告警群 group_openid',
          description: '每行一个，严重告警推送到这些群。',
          'x-widget': 'lines',
          'x-apply': 'agent',
        },
        welcome: {
          anyOf: [{ type: 'string' }, { type: 'null' }],
          title: '入群欢迎语',
          description: '留空则不发送。',
          'x-advanced': true,
          'x-apply': 'agent',
        },
      },
      required: ['enabled'],
    },
    edge: {
      type: 'object',
      title: '边缘（frp）',
      properties: {
        bind_port: {
          type: 'integer',
          title: 'frps 控制端口',
          minimum: 1,
          maximum: 65535,
          'x-apply': 'agent',
        },
        proxy_protocol: {
          type: 'boolean',
          title: '启用 PROXY protocol',
          description: '让 MC 看到玩家真实 IP。',
          'x-apply': 'mc',
        },
        allow_ports: {
          type: 'string',
          title: '放行端口范围',
          description: '形如 25565-25600,7000。',
          'x-advanced': true,
          'x-apply': 'agent',
        },
      },
      required: ['bind_port'],
    },
    retention: {
      type: 'object',
      title: '数据保留',
      properties: {
        events_days: {
          type: 'integer',
          title: '事件保留天数',
          minimum: 7,
          maximum: 3650,
          'x-unit': '天',
          'x-apply': 'agent',
        },
        log_level: {
          type: 'string',
          enum: ['debug', 'info', 'warning', 'error'],
          title: '日志级别',
          'x-apply': 'agent',
        },
      },
      required: ['events_days', 'log_level'],
    },
  },
  required: ['server', 'ops', 'security', 'bot', 'edge', 'retention'],
};

const sections = [
  { key: 'server', title: '服务端', description: '启动参数、端口、出生点等。' },
  { key: 'ops', title: '运维', description: '定时重启、备份与 restic。' },
  { key: 'security', title: '安全', description: '告警时段与自动封禁策略。' },
  { key: 'bot', title: 'QQ 机器人', description: '机器人开关、管理员与告警群。' },
  { key: 'edge', title: '边缘（frp）', description: 'frps 端口与 PROXY protocol。' },
  { key: 'retention', title: '数据保留', description: '事件与日志保留策略。' },
];

// yaml 默认值 —— reset 时回退到这里。
const defaults = {
  server: {
    java_opts: ['-Xms8G', '-Xmx8G', '-XX:+UseG1GC', '-XX:+ParallelRefProcEnabled'],
    port: 25565,
    motd: '§6米奇喵的服务器 §7| §a1.21.11',
    spawn: [0, 64, 0],
    allow_flight: true,
  },
  ops: {
    restart_time: '05:00',
    keep_backups: 7,
    warn_minutes: 5,
    restic: { enabled: true, interval_minutes: 60, keep_hourly: 48, remote: null },
  },
  security: {
    night_start_hour: 1,
    night_end_hour: 6,
    auto_ban_scanners: true,
    brute_force_threshold: 8,
  },
  bot: {
    enabled: true,
    admin_openids: ['E8041F2A9C7B5786'],
    alert_groups: ['C0FFEE00C0FFEE00'],
    welcome: null,
  },
  edge: {
    bind_port: 7000,
    proxy_protocol: true,
    allow_ports: '25565-25600,7000',
  },
  retention: {
    events_days: 90,
    log_level: 'info',
  },
};

// 覆盖层：面板改过的叶子 path -> 值。初始几条示范「已自定义」+ pending。
let overlay: Record<string, unknown> = {
  'server.java_opts': ['-Xms10G', '-Xmx10G', '-XX:+UseG1GC', '-XX:+ParallelRefProcEnabled'],
  'ops.restart_time': '05:30',
};

// agent 运行中尚未采用的改动（示范：java_opts 下次开服生效，restart_time 重启 agent 生效）。
const pendingPaths = new Map<string, 'agent' | 'mc' | 'live'>([
  ['ops.restart_time', 'agent'],
  ['server.java_opts', 'mc'],
]);

const readonly = {
  mode: 'control',
  data_dir: '/home/mc/.local/share/mcops',
  api: '127.0.0.1:8765',
  config_file: '/home/mc/.config/mcops/mcops.yaml',
};

interface HistoryRow {
  id: number;
  ts: string;
  user: string;
  changed: string[];
  note: string;
  overlay: Record<string, unknown>;
}

let historySeq = 2;
const history: HistoryRow[] = [
  {
    id: 2,
    ts: '2026-10-09T15:35:00+08:00',
    user: 'admin',
    changed: ['ops.restart_time'],
    note: '把每日重启挪到 05:30',
    overlay: { 'server.java_opts': ['-Xms10G', '-Xmx10G', '-XX:+UseG1GC', '-XX:+ParallelRefProcEnabled'], 'ops.restart_time': '05:30' },
  },
  {
    id: 1,
    ts: '2026-10-09T14:20:00+08:00',
    user: 'admin',
    changed: ['server.java_opts'],
    note: '升到 10G 堆',
    overlay: { 'server.java_opts': ['-Xms10G', '-Xmx10G', '-XX:+UseG1GC', '-XX:+ParallelRefProcEnabled'] },
  },
];

// ---- path helpers -----------------------------------------------------------

function getLeaf(obj: Record<string, unknown>, path: string): unknown {
  let cur: unknown = obj;
  for (const p of path.split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}

function clone<T>(v: T): T {
  return structuredClone(v);
}

/** 生效配置 = defaults 深拷贝，再逐条叠加 overlay 叶子。 */
function effectiveValues(ov: Record<string, unknown> = overlay): Record<string, unknown> {
  const out = clone(defaults) as Record<string, unknown>;
  for (const [path, value] of Object.entries(ov)) {
    const parts = path.split('.');
    let cur = out;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const k = parts[i];
      if (typeof cur[k] !== 'object' || cur[k] === null) cur[k] = {};
      cur = cur[k] as Record<string, unknown>;
    }
    cur[parts[parts.length - 1]] = clone(value);
  }
  return out;
}

/** 把 PUT values（部分嵌套结构）拍平成叶子 path -> 值。 */
function flattenLeaves(values: Record<string, unknown>, prefix = ''): Array<[string, unknown]> {
  const out: Array<[string, unknown]> = [];
  for (const [k, v] of Object.entries(values)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      out.push(...flattenLeaves(v as Record<string, unknown>, path));
    } else {
      out.push([path, v]);
    }
  }
  return out;
}

function applyKindFor(path: string): 'agent' | 'mc' | 'live' {
  const node = getLeaf(schemaFlat, path) as { 'x-apply'?: 'agent' | 'mc' | 'live' } | undefined;
  return node?.['x-apply'] ?? 'live';
}

// 预先把 schema 展开成 path -> 节点（只为取 x-apply）。
const schemaFlat: Record<string, unknown> = {};
(function flattenSchema() {
  const props = schema.properties ?? {};
  for (const [sk, sec] of Object.entries(props)) {
    const sprops = (sec as RootSchema).properties ?? {};
    for (const [fk, field] of Object.entries(sprops)) {
      let node = field;
      if (node.$ref) {
        const m = /^#\/\$defs\/(.+)$/.exec(node.$ref);
        if (m && schema.$defs) node = schema.$defs[m[1]];
      }
      // nested object (restic): descend one more level
      if (node.properties) {
        for (const [nk, nfield] of Object.entries(node.properties)) {
          schemaFlat[`${sk}.${fk}.${nk}`] = nfield;
        }
      }
      schemaFlat[`${sk}.${fk}`] = field.$ref ? node : field;
    }
  }
})();

function deepEqualJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function buildResponse(): ConfigResponse {
  return {
    schema: clone(schema),
    sections: clone(sections),
    values: effectiveValues(),
    overridden: Object.keys(overlay),
    pending: [...pendingPaths].map(([path, apply]) => ({ path, apply })),
    readonly: clone(readonly),
  };
}

function buildUpdateResult(changed: Array<{ path: string; apply: 'agent' | 'mc' | 'live' }>): ConfigUpdateResult {
  return {
    ok: true,
    changed,
    pending: [...pendingPaths].map(([path, apply]) => ({ path, apply })),
    values: effectiveValues(),
    overridden: Object.keys(overlay),
  };
}

// ---- public mock operations -------------------------------------------------

export function mockConfig(): ConfigResponse {
  return buildResponse();
}

export function mockUpdateConfig(values: Record<string, unknown>, note?: string): ConfigUpdateResult {
  const current = effectiveValues();
  const changed: Array<{ path: string; apply: 'agent' | 'mc' | 'live' }> = [];
  for (const [path, value] of flattenLeaves(values)) {
    // 校验：端口范围（示范 422）。
    if (path === 'server.port' && (typeof value !== 'number' || value < 1 || value > 65535)) {
      throw new ApiError(422, 'server.port：必须在 1-65535 之间');
    }
    if (deepEqualJson(getLeaf(current, path), value)) continue;
    const def = getLeaf(defaults as Record<string, unknown>, path);
    if (deepEqualJson(def, value)) {
      delete overlay[path]; // 改回默认值 = 移除覆盖
    } else {
      overlay[path] = clone(value);
    }
    const apply = applyKindFor(path);
    changed.push({ path, apply });
    if (apply !== 'live') pendingPaths.set(path, apply);
  }
  if (changed.length > 0) {
    historySeq += 1;
    history.unshift({
      id: historySeq,
      ts: new Date().toISOString(),
      user: 'admin',
      changed: changed.map((c) => c.path),
      note: note ?? '',
      overlay: clone(overlay),
    });
  }
  return buildUpdateResult(changed);
}

export function mockResetConfig(paths: string[]): ConfigUpdateResult {
  const changed: Array<{ path: string; apply: 'agent' | 'mc' | 'live' }> = [];
  for (const path of paths) {
    if (path in overlay) {
      delete overlay[path];
      const apply = applyKindFor(path);
      changed.push({ path, apply });
      if (apply !== 'live') pendingPaths.set(path, apply);
    }
  }
  if (changed.length > 0) {
    historySeq += 1;
    history.unshift({
      id: historySeq,
      ts: new Date().toISOString(),
      user: 'admin',
      changed: changed.map((c) => c.path),
      note: '恢复默认',
      overlay: clone(overlay),
    });
  }
  return buildUpdateResult(changed);
}

export function mockHistory(limit = 50): ConfigHistoryResponse {
  return {
    items: history.slice(0, limit).map((h) => ({
      id: h.id,
      ts: h.ts,
      user: h.user,
      changed: [...h.changed],
      note: h.note,
    })),
  };
}

export function mockRestoreHistory(id: number): ConfigUpdateResult {
  const row = history.find((h) => h.id === id);
  if (!row) throw new ApiError(404, '找不到该历史版本');
  const before = clone(overlay);
  overlay = clone(row.overlay);
  const changed: Array<{ path: string; apply: 'agent' | 'mc' | 'live' }> = [];
  const allPaths = new Set([...Object.keys(before), ...Object.keys(overlay)]);
  for (const path of allPaths) {
    if (!deepEqualJson(before[path], overlay[path])) {
      const apply = applyKindFor(path);
      changed.push({ path, apply });
      if (apply !== 'live') pendingPaths.set(path, apply);
    }
  }
  historySeq += 1;
  history.unshift({
    id: historySeq,
    ts: new Date().toISOString(),
    user: 'admin',
    changed: changed.map((c) => c.path),
    note: `恢复到版本 #${id}`,
    overlay: clone(overlay),
  });
  return buildUpdateResult(changed);
}

export function mockRestartAgent(): { accepted: true } {
  // 清空 agent 类 pending（模拟 agent 重启后采用）。
  for (const [path, apply] of [...pendingPaths]) {
    if (apply === 'agent') pendingPaths.delete(path);
  }
  return { accepted: true };
}

export function mockCapabilities(): Capabilities {
  return {
    mc_version: '1.21.11',
    loader: 'fabric',
    loader_version: '0.19.2',
    mods: { easyauth: true, carpet: true, proxy_protocol: true, minimotd: true },
    proxy_source: '10.88.88.1',
    features: { edge: true, bot: true, restic: true, systemd: true, flight_watch: true },
  };
}

// ---- server.properties ------------------------------------------------------

const propsState: Record<string, string | number | boolean | null> = {
  difficulty: 'normal',
  gamemode: 'survival',
  'max-players': 20,
  'view-distance': 12,
  'simulation-distance': 10,
  pvp: true,
  hardcore: false,
  'spawn-protection': 0,
  'level-name': 'world',
  'enable-rcon': true,
  'rcon.port': 25575,
  'rcon.password': null,
  'server-port': 25565,
  motd: '§6米奇喵的服务器',
  'enforce-whitelist': true,
  'player-idle-timeout': 0,
  'network-compression-threshold': 256,
  'custom-unknown-key': 'legacy-value',
};

const propsPending = new Set<string>(['view-distance']);

function buildProperties(): PropertiesResponse {
  const def = (
    key: string,
    extra: Partial<PropertiesResponse['items'][number]>,
  ): PropertiesResponse['items'][number] => ({
    key,
    value: propsState[key] ?? null,
    known: true,
    type: 'string',
    group: '其它',
    managed: false,
    ...extra,
  });
  const items: PropertiesResponse['items'] = [
    def('difficulty', {
      type: 'enum',
      options: ['peaceful', 'easy', 'normal', 'hard'],
      group: '玩法',
      title: '难度',
      description: '世界难度。',
    }),
    def('gamemode', {
      type: 'enum',
      options: ['survival', 'creative', 'adventure', 'spectator'],
      group: '玩法',
      title: '默认游戏模式',
    }),
    def('hardcore', { type: 'bool', group: '玩法', title: '极限模式', description: '死亡即旁观。' }),
    def('pvp', { type: 'bool', group: '玩法', title: '玩家对战' }),
    def('level-name', { type: 'string', group: '世界', title: '存档名' }),
    def('spawn-protection', {
      type: 'int',
      min: 0,
      max: 1000,
      group: '世界',
      title: '出生点保护半径',
    }),
    def('max-players', { type: 'int', min: 1, max: 200, group: '玩家与权限', title: '最大玩家数' }),
    def('enforce-whitelist', {
      type: 'bool',
      group: '玩家与权限',
      title: '强制白名单',
    }),
    def('player-idle-timeout', {
      type: 'int',
      min: 0,
      max: 1440,
      group: '玩家与权限',
      title: '挂机踢出（分钟）',
    }),
    def('view-distance', {
      type: 'int',
      min: 3,
      max: 32,
      group: '网络与性能',
      title: '视距',
      description: '区块视距，影响带宽与性能。',
    }),
    def('simulation-distance', {
      type: 'int',
      min: 3,
      max: 32,
      group: '网络与性能',
      title: '模拟距离',
    }),
    def('network-compression-threshold', {
      type: 'int',
      min: -1,
      max: 1024,
      group: '网络与性能',
      title: '压缩阈值',
    }),
    // managed keys
    def('enable-rcon', {
      type: 'bool',
      group: '网络与性能',
      title: '启用 RCON',
      managed: true,
      managed_reason: '由 mc-ops 管理：面板与机器人通过 RCON 执行命令，不能关闭。',
    }),
    def('rcon.port', {
      type: 'int',
      min: 1,
      max: 65535,
      group: '网络与性能',
      title: 'RCON 端口',
      managed: true,
      managed_reason: '由 mc-ops 管理。',
    }),
    def('rcon.password', {
      type: 'string',
      group: '网络与性能',
      title: 'RCON 密码',
      managed: true,
      managed_reason: '由 mc-ops 管理，面板不显示。',
      sensitive: true,
      value: null,
    }),
    def('server-port', {
      type: 'int',
      min: 1,
      max: 65535,
      group: '网络与性能',
      title: '游戏端口',
      managed: true,
      managed_reason: '在「服务器配置」里改游戏端口，会同步到这里。',
    }),
    def('motd', {
      type: 'string',
      group: '网络与性能',
      title: 'MOTD',
      managed: true,
      managed_reason: '已开启 MOTD 同步：在「服务器配置」里修改。',
    }),
    // unknown key
    def('custom-unknown-key', { known: false, type: 'string', group: '其它' }),
  ];
  return {
    path: '/home/steve/minecraft/server.properties',
    items,
    pending: [...propsPending],
  };
}

export function mockProperties(): PropertiesResponse {
  return buildProperties();
}

export function mockUpdateProperties(
  changes: Record<string, string | number | boolean>,
): PropertiesUpdateResult {
  const managed = new Set(['enable-rcon', 'rcon.port', 'rcon.password', 'server-port', 'motd']);
  const changed: string[] = [];
  for (const [key, value] of Object.entries(changes)) {
    if (managed.has(key)) {
      throw new ApiError(422, `${key} 由 mc-ops 管理，不能在此修改`);
    }
    if (key === 'view-distance' && (typeof value !== 'number' || value < 3 || value > 32)) {
      throw new ApiError(422, 'view-distance：必须在 3-32 之间');
    }
    propsState[key] = value;
    propsPending.add(key);
    changed.push(key);
  }
  const stamp = new Date()
    .toISOString()
    .replace(/[-:T]/g, '')
    .slice(0, 14)
    .replace(/(\d{8})(\d{6})/, '$1-$2');
  return { ok: true, changed, backup: `server.properties.bak-${stamp}` };
}

// ---- account / totp / secrets ----------------------------------------------

const totpState = { enabled: false };

export function mockTotpStatus(): TotpStatus {
  return { enabled: totpState.enabled };
}

export function mockTotpSetup(): TotpSetupResponse {
  const secret = 'JBSWY3DPEHPK3PXP';
  return {
    secret,
    otpauth_uri: `otpauth://totp/mc-ops:admin?secret=${secret}&issuer=mc-ops`,
  };
}

export function mockTotpConfirm(code: string): TotpStatus {
  if (!/^\d{6}$/.test(code)) throw new ApiError(422, '验证码必须是 6 位数字');
  if (code === '000000') throw new ApiError(422, '验证码不正确');
  totpState.enabled = true;
  return { enabled: true };
}

export function mockTotpDisable(password: string): TotpStatus {
  if (password !== 'admin') throw new ApiError(422, '密码不正确');
  totpState.enabled = false;
  return { enabled: false };
}

export function mockChangePassword(oldPassword: string, newPassword: string): void {
  if (oldPassword !== 'admin') throw new ApiError(422, '原密码不正确');
  if (newPassword.length < 12) throw new ApiError(422, '新密码至少 12 位');
}

const secretsState: Record<string, boolean> = {
  qq_appid: true,
  qq_secret: true,
  restic_password: true,
  smtp_password: false,
};

export function mockSecrets(): SecretsResponse {
  return {
    items: [
      { key: 'qq_appid', title: 'QQ 机器人 AppID', set: secretsState.qq_appid, editable: true, apply: 'agent' },
      { key: 'qq_secret', title: 'QQ 机器人 AppSecret', set: secretsState.qq_secret, editable: true, apply: 'agent' },
      { key: 'smtp_password', title: 'SMTP 授权码', set: secretsState.smtp_password, editable: true, apply: 'live' },
      {
        key: 'restic_password',
        title: 'restic 仓库密码',
        set: secretsState.restic_password,
        editable: false,
        note: '改密需 restic key passwd，面板不支持。',
      },
    ],
  };
}

export function mockUpdateSecrets(values: Record<string, string>): SecretsUpdateResult {
  const pending: Array<{ path: string; apply: 'agent' | 'mc' | 'live' }> = [];
  for (const [key, value] of Object.entries(values)) {
    if (key === 'restic_password') throw new ApiError(422, 'restic 仓库密码不可通过面板修改');
    if (value === '') continue; // 空 = 不改
    secretsState[key] = true;
    if (key === 'qq_appid' || key === 'qq_secret') pending.push({ path: `secrets.${key}`, apply: 'agent' });
  }
  return { ok: true, pending };
}

// ---- ConfigApi 形状的 mock 客户端 -------------------------------------------
// index.ts 按 VITE_MOCK 在 configApi（真实）与此之间切换。延迟 + Promise 包装，
// 和 mods.ts / network.ts 的 mock 客户端一致。
import type { ConfigApi } from './config';

const delay = (ms = 120) => new Promise<void>((r) => setTimeout(r, ms));

export const configMockApi: ConfigApi = {
  async config() {
    await delay();
    return mockConfig();
  },
  async updateConfig(values, note) {
    await delay();
    return mockUpdateConfig(values, note);
  },
  async resetConfig(paths) {
    await delay();
    return mockResetConfig(paths);
  },
  async history(limit = 50) {
    await delay();
    return mockHistory(limit);
  },
  async restoreHistory(id) {
    await delay();
    return mockRestoreHistory(id);
  },
  async restartAgent() {
    await delay(300);
    return mockRestartAgent();
  },
  async capabilities() {
    await delay();
    return mockCapabilities();
  },
  async properties() {
    await delay();
    return mockProperties();
  },
  async updateProperties(changes) {
    await delay();
    return mockUpdateProperties(changes);
  },
  async changePassword(oldPassword, newPassword) {
    await delay();
    mockChangePassword(oldPassword, newPassword);
  },
  async totpStatus() {
    await delay();
    return mockTotpStatus();
  },
  async totpSetup() {
    await delay();
    return mockTotpSetup();
  },
  async totpConfirm(code) {
    await delay();
    return mockTotpConfirm(code);
  },
  async totpDisable(password) {
    await delay();
    return mockTotpDisable(password);
  },
  async secrets() {
    await delay();
    return mockSecrets();
  },
  async updateSecrets(values) {
    await delay();
    return mockUpdateSecrets(values);
  },
};
