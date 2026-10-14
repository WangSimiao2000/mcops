// Pure, React-free logic for the schema-driven 服务器配置 tab. Fully unit-tested.
// Walks the pydantic JSON Schema (resolving $ref/$defs) into a flat list of
// editable fields per section, and carries value get/set, validation and the
// memory (-Xms/-Xmx) widget parsing.

import type {
  ApplyKind,
  RootSchema,
  SchemaNode,
  WidgetKind,
} from '@/api/config';

/** 字段的解析后基础类型。unknown = 无法识别（回退为 JSON 文本框）。 */
export type FieldKind =
  | 'object'
  | 'string'
  | 'integer'
  | 'number'
  | 'boolean'
  | 'enum'
  | 'string-array'
  | 'int-tuple'
  | 'unknown';

export interface FieldModel {
  /** 点分路径，如 "server.java_opts"。 */
  path: string;
  /** 末段 key，如 "java_opts"。 */
  key: string;
  label: string;
  description?: string;
  kind: FieldKind;
  apply?: ApplyKind;
  advanced: boolean;
  unit?: string;
  widget?: WidgetKind;
  nullable: boolean;
  required: boolean;
  // 约束
  min?: number;
  max?: number;
  enumValues?: Array<string | number>;
  /** int-tuple 的元素个数（spawn = 3）。 */
  tupleLen?: number;
}

/** 一个 section 的字段列表（已按 schema 声明顺序展开）。 */
export interface SectionModel {
  key: string;
  title: string;
  description?: string;
  fields: FieldModel[];
}

/** 解析 "#/$defs/ServerConfig" 为 $defs 节点；非 $ref 原样返回。 */
export function resolveRef(schema: RootSchema, node: SchemaNode): SchemaNode {
  let cur = node;
  let guard = 0;
  while (cur && cur.$ref && guard < 20) {
    const m = /^#\/\$defs\/(.+)$/.exec(cur.$ref);
    if (!m || !schema.$defs) break;
    const target = schema.$defs[m[1]];
    if (!target) break;
    // 合并 $ref 节点上可能并存的 title/description/扩展字段（pydantic 常把它们放在
    // 引用处而非 $def 上）。
    const { $ref: _ref, ...rest } = cur;
    cur = { ...target, ...rest };
    guard += 1;
  }
  return cur;
}

/**
 * 把 anyOf/oneOf 里的「真实类型」与「null」拆开。
 * 返回 { node: 去掉 null 后的有效类型节点, nullable }。
 */
export function unwrapNullable(
  schema: RootSchema,
  node: SchemaNode,
): { node: SchemaNode; nullable: boolean } {
  const variants = node.anyOf ?? node.oneOf;
  if (!variants || variants.length === 0) return { node, nullable: false };
  let nullable = false;
  const real: SchemaNode[] = [];
  for (const v of variants) {
    const r = resolveRef(schema, v);
    if (r.type === 'null') {
      nullable = true;
    } else {
      real.push(r);
    }
  }
  if (real.length === 1) {
    // 把外层的 title/description/扩展字段带到唯一的有效类型上。
    const { anyOf: _a, oneOf: _o, ...carry } = node;
    return { node: { ...real[0], ...stripUndefined(carry) }, nullable };
  }
  // 多个非 null 变体（少见）：保持原节点，仅标记 nullable。
  return { node, nullable };
}

function stripUndefined(obj: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) if (v !== undefined) out[k] = v;
  return out;
}

/** 判断单层（非对象）节点的 FieldKind。 */
export function classify(node: SchemaNode): FieldKind {
  // 三元 int 元组（spawn）。
  if (Array.isArray(node.prefixItems) && node.prefixItems.length > 0) return 'int-tuple';
  if (node.enum && node.enum.length > 0) return 'enum';
  if (node.const !== undefined) return 'enum';
  const t = Array.isArray(node.type) ? node.type.find((x) => x !== 'null') : node.type;
  switch (t) {
    case 'object':
      return 'object';
    case 'boolean':
      return 'boolean';
    case 'integer':
      return 'integer';
    case 'number':
      return 'number';
    case 'string':
      return 'string';
    case 'array': {
      const items = Array.isArray(node.items) ? node.items[0] : node.items;
      const it = items?.type;
      if (it === 'integer' || it === 'number') return 'int-tuple';
      return 'string-array';
    }
    default:
      if (node.properties) return 'object';
      return 'unknown';
  }
}

function numConstraint(node: SchemaNode): { min?: number; max?: number } {
  const min = node.minimum ?? node.exclusiveMinimum;
  const max = node.maximum ?? node.exclusiveMaximum;
  const out: { min?: number; max?: number } = {};
  if (typeof min === 'number') out.min = min;
  if (typeof max === 'number') out.max = max;
  return out;
}

/**
 * 递归展开一个对象节点为 FieldModel[]。object 字段本身也产出一个 kind:'object'
 * 的分组标记（带 label/description），其子字段紧随其后、path 带前缀。
 */
function walk(
  schema: RootSchema,
  node: SchemaNode,
  prefix: string,
  requiredHere: Set<string>,
  out: FieldModel[],
): void {
  const props = node.properties ?? {};
  for (const [key, rawChild] of Object.entries(props)) {
    const path = prefix ? `${prefix}.${key}` : key;
    const refResolved = resolveRef(schema, rawChild);
    const { node: child, nullable } = unwrapNullable(schema, refResolved);
    const kind = classify(child);
    const base: FieldModel = {
      path,
      key,
      label: child.title ?? rawChild.title ?? key,
      description: child.description ?? rawChild.description,
      kind,
      apply: (child['x-apply'] ?? rawChild['x-apply']) as ApplyKind | undefined,
      advanced: Boolean(child['x-advanced'] ?? rawChild['x-advanced']),
      unit: (child['x-unit'] ?? rawChild['x-unit']) as string | undefined,
      widget: (child['x-widget'] ?? rawChild['x-widget']) as WidgetKind | undefined,
      nullable,
      required: requiredHere.has(key),
    };
    if (kind === 'object') {
      out.push(base);
      walk(schema, child, path, new Set(child.required ?? []), out);
      continue;
    }
    if (kind === 'enum') {
      const values = (child.enum ?? (child.const !== undefined ? [child.const] : [])) as Array<
        string | number
      >;
      base.enumValues = values;
    }
    if (kind === 'integer' || kind === 'number') {
      Object.assign(base, numConstraint(child));
    }
    if (kind === 'int-tuple') {
      base.tupleLen = Array.isArray(child.prefixItems)
        ? child.prefixItems.length
        : (child.minItems ?? 3);
      const first = Array.isArray(child.prefixItems)
        ? child.prefixItems[0]
        : Array.isArray(child.items)
          ? child.items[0]
          : child.items;
      if (first) Object.assign(base, numConstraint(resolveRef(schema, first)));
    }
    out.push(base);
  }
}

/**
 * 从根 schema + sections 顺序，构建每个 section 的字段模型。
 * 每个 section 的 key 对应 root.properties[key]（一个嵌套对象）。
 */
export function buildSections(
  schema: RootSchema,
  sections: { key: string; title: string; description?: string }[],
): SectionModel[] {
  const rootProps = schema.properties ?? {};
  const out: SectionModel[] = [];
  for (const s of sections) {
    const raw = rootProps[s.key];
    if (!raw) {
      out.push({ key: s.key, title: s.title, description: s.description, fields: [] });
      continue;
    }
    const node = resolveRef(schema, raw);
    const fields: FieldModel[] = [];
    walk(schema, node, s.key, new Set(node.required ?? []), fields);
    out.push({ key: s.key, title: s.title, description: s.description, fields });
  }
  return out;
}

// ---- 值读写（点分路径） -----------------------------------------------------

export function getAtPath(values: Record<string, unknown>, path: string): unknown {
  const parts = path.split('.');
  let cur: unknown = values;
  for (const p of parts) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}

/** 返回一个带有新值的深拷贝；不修改输入。 */
export function setAtPath(
  values: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  const parts = path.split('.');
  const root: Record<string, unknown> = { ...values };
  let cur = root;
  for (let i = 0; i < parts.length - 1; i += 1) {
    const p = parts[i];
    const next = cur[p];
    cur[p] = next && typeof next === 'object' && !Array.isArray(next) ? { ...(next as object) } : {};
    cur = cur[p] as Record<string, unknown>;
  }
  cur[parts[parts.length - 1]] = value;
  return root;
}

// ---- memory widget：-Xms/-Xmx 解析 ------------------------------------------

const SIZE_RE = /^(\d+(?:\.\d+)?)([kKmMgGtT])?$/;

/** 把 "8G"/"8192M"/"8g" 解析为 GB 数（浮点）。无法解析返回 null。 */
export function parseJvmSizeGb(token: string): number | null {
  const m = SIZE_RE.exec(token.trim());
  if (!m) return null;
  const n = Number(m[1]);
  if (!Number.isFinite(n)) return null;
  const unit = (m[2] ?? 'm').toLowerCase(); // JVM 默认无单位按字节，但 java_opts 几乎总带单位；缺省按 M 处理
  const factor: Record<string, number> = {
    k: 1 / (1024 * 1024),
    m: 1 / 1024,
    g: 1,
    t: 1024,
  };
  return n * factor[unit];
}

/** 从 java_opts 数组里读出 -Xmx 对应的 GB 值（优先 Xmx，退回 Xms）。 */
export function readMemoryGb(opts: string[]): number | null {
  const find = (flag: string): number | null => {
    for (const o of opts) {
      if (o.startsWith(flag)) {
        return parseJvmSizeGb(o.slice(flag.length));
      }
    }
    return null;
  };
  return find('-Xmx') ?? find('-Xms');
}

/**
 * 在保留其它 JVM 参数的前提下，把 -Xms/-Xmx 都设为 `${gb}G`。
 * 若原数组没有 -Xms/-Xmx，则追加这两项。整数 GB 用 "8G"，否则用 MB（"6144M"）避免小数。
 */
export function writeMemoryGb(opts: string[], gb: number): string[] {
  const token =
    Number.isInteger(gb) ? `${gb}G` : `${Math.round(gb * 1024)}M`;
  const out: string[] = [];
  let sawXms = false;
  let sawXmx = false;
  for (const o of opts) {
    if (o.startsWith('-Xms')) {
      out.push(`-Xms${token}`);
      sawXms = true;
    } else if (o.startsWith('-Xmx')) {
      out.push(`-Xmx${token}`);
      sawXmx = true;
    } else {
      out.push(o);
    }
  }
  if (!sawXms) out.push(`-Xms${token}`);
  if (!sawXmx) out.push(`-Xmx${token}`);
  return out;
}

/** java_opts 里除 -Xms/-Xmx 外的其它参数（用于 UI 展示「其它参数保持不变」）。 */
export function otherJvmOpts(opts: string[]): string[] {
  return opts.filter((o) => !o.startsWith('-Xms') && !o.startsWith('-Xmx'));
}

// ---- 校验 -------------------------------------------------------------------

/** 单字段客户端校验；返回错误文案或 null。 */
export function validateField(field: FieldModel, value: unknown): string | null {
  const empty = value === '' || value === undefined || value === null;
  if (field.required && empty && !field.nullable) {
    return `${field.label}：必填`;
  }
  if (empty) return null;
  if (field.kind === 'integer' || field.kind === 'number') {
    const n = typeof value === 'number' ? value : Number(value);
    if (!Number.isFinite(n)) return `${field.label}：必须是数字`;
    if (field.kind === 'integer' && !Number.isInteger(n)) return `${field.label}：必须是整数`;
    if (field.min !== undefined && n < field.min) return `${field.label}：不能小于 ${field.min}`;
    if (field.max !== undefined && n > field.max) return `${field.label}：不能大于 ${field.max}`;
  }
  if (field.kind === 'int-tuple' && Array.isArray(value)) {
    for (const el of value) {
      const n = Number(el);
      if (!Number.isInteger(n)) return `${field.label}：每一项都必须是整数`;
      if (field.min !== undefined && n < field.min) return `${field.label}：不能小于 ${field.min}`;
      if (field.max !== undefined && n > field.max) return `${field.label}：不能大于 ${field.max}`;
    }
  }
  return null;
}

/** 深相等（用于 dirty 判断，值皆为 JSON 可序列化）。 */
export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a && b && typeof a === 'object') {
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    if (Array.isArray(a) && Array.isArray(b)) {
      if (a.length !== b.length) return false;
      return a.every((x, i) => deepEqual(x, b[i]));
    }
    const ak = Object.keys(a as object);
    const bk = Object.keys(b as object);
    if (ak.length !== bk.length) return false;
    return ak.every((k) =>
      deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]),
    );
  }
  return false;
}

/** 把一个 section 的 draft 与原始 values 比较，返回有改动的叶子 path。 */
export function changedPaths(
  fields: FieldModel[],
  original: Record<string, unknown>,
  draft: Record<string, unknown>,
): string[] {
  const out: string[] = [];
  for (const f of fields) {
    if (f.kind === 'object') continue;
    if (!deepEqual(getAtPath(original, f.path), getAtPath(draft, f.path))) out.push(f.path);
  }
  return out;
}

export const APPLY_LABEL: Record<ApplyKind, string> = {
  agent: '重启 agent 生效',
  mc: '下次开服生效',
  live: '立即生效',
};

export const APPLY_TONE: Record<ApplyKind, 'yellow' | 'blue' | 'green'> = {
  agent: 'yellow',
  mc: 'blue',
  live: 'green',
};
