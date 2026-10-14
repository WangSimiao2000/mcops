import { describe, expect, it } from 'vitest';
import type { RootSchema } from '@/api/config';
import {
  buildSections,
  changedPaths,
  classify,
  deepEqual,
  getAtPath,
  otherJvmOpts,
  parseJvmSizeGb,
  readMemoryGb,
  resolveRef,
  setAtPath,
  unwrapNullable,
  validateField,
  writeMemoryGb,
} from './schemaForm';

// 一个贴近 pydantic model_json_schema 输出的小 schema：$defs + $ref、嵌套对象、
// enum（Literal）、字符串数组（lines/memory）、三元 int 元组（spawn）、可空字段。
const schema: RootSchema = {
  type: 'object',
  $defs: {
    Restic: {
      type: 'object',
      title: 'restic',
      properties: {
        enabled: { type: 'boolean', title: '启用', 'x-apply': 'agent' },
        interval_minutes: {
          type: 'integer',
          title: '间隔',
          minimum: 15,
          maximum: 1440,
          'x-unit': '分钟',
        },
        remote: { anyOf: [{ type: 'string' }, { type: 'null' }], title: '远端', 'x-advanced': true },
      },
      required: ['enabled', 'interval_minutes'],
    },
  },
  properties: {
    server: {
      type: 'object',
      title: '服务端',
      properties: {
        java_opts: { type: 'array', items: { type: 'string' }, title: 'JVM', 'x-widget': 'memory' },
        port: { type: 'integer', title: '端口', minimum: 1, maximum: 65535 },
        spawn: {
          prefixItems: [{ type: 'integer' }, { type: 'integer' }, { type: 'integer' }],
          minItems: 3,
          maxItems: 3,
          title: '出生点',
        },
      },
      required: ['java_opts', 'port'],
    },
    ops: {
      type: 'object',
      title: '运维',
      properties: {
        restart_time: { type: 'string', title: '重启时间', 'x-widget': 'time' },
        admins: { type: 'array', items: { type: 'string' }, title: '管理员', 'x-widget': 'lines' },
        log_level: { type: 'string', enum: ['debug', 'info', 'warning'], title: '日志级别' },
        restic: { $ref: '#/$defs/Restic' },
      },
      required: ['restart_time'],
    },
  },
};

const sections = [
  { key: 'server', title: '服务端' },
  { key: 'ops', title: '运维' },
];

describe('resolveRef', () => {
  it('resolves #/$defs/* and carries title at the ref site', () => {
    const node = resolveRef(schema, { $ref: '#/$defs/Restic', title: '覆盖标题' });
    expect(node.title).toBe('覆盖标题'); // ref-site title 覆盖 $def title
    expect(node.properties?.enabled).toBeDefined();
  });
});

describe('unwrapNullable', () => {
  it('splits anyOf [T, null] into the real type + nullable flag', () => {
    const { node, nullable } = unwrapNullable(schema, {
      anyOf: [{ type: 'string' }, { type: 'null' }],
      title: '远端',
    });
    expect(nullable).toBe(true);
    expect(node.type).toBe('string');
    expect(node.title).toBe('远端');
  });
});

describe('classify', () => {
  it('recognizes each supported kind', () => {
    expect(classify({ type: 'object', properties: {} })).toBe('object');
    expect(classify({ type: 'boolean' })).toBe('boolean');
    expect(classify({ type: 'integer' })).toBe('integer');
    expect(classify({ type: 'string' })).toBe('string');
    expect(classify({ enum: ['a', 'b'] })).toBe('enum');
    expect(classify({ type: 'array', items: { type: 'string' } })).toBe('string-array');
    expect(classify({ prefixItems: [{ type: 'integer' }, { type: 'integer' }] })).toBe('int-tuple');
  });
});

describe('buildSections', () => {
  const built = buildSections(schema, sections);

  it('expands sections in declared order', () => {
    expect(built.map((s) => s.key)).toEqual(['server', 'ops']);
  });

  it('flattens leaf fields with dotted paths and carries extensions', () => {
    const server = built[0];
    const paths = server.fields.map((f) => f.path);
    expect(paths).toContain('server.java_opts');
    expect(paths).toContain('server.port');
    expect(paths).toContain('server.spawn');

    const java = server.fields.find((f) => f.path === 'server.java_opts')!;
    expect(java.kind).toBe('string-array');
    expect(java.widget).toBe('memory');

    const port = server.fields.find((f) => f.path === 'server.port')!;
    expect(port.kind).toBe('integer');
    expect(port.min).toBe(1);
    expect(port.max).toBe(65535);

    const spawn = server.fields.find((f) => f.path === 'server.spawn')!;
    expect(spawn.kind).toBe('int-tuple');
    expect(spawn.tupleLen).toBe(3);
  });

  it('resolves an enum (Literal) field with its values', () => {
    const ops = built[1];
    const lvl = ops.fields.find((f) => f.path === 'ops.log_level')!;
    expect(lvl.kind).toBe('enum');
    expect(lvl.enumValues).toEqual(['debug', 'info', 'warning']);
  });

  it('resolves a $ref nested object into an object marker + child leaves', () => {
    const ops = built[1];
    const restic = ops.fields.find((f) => f.path === 'ops.restic')!;
    expect(restic.kind).toBe('object');
    const child = ops.fields.find((f) => f.path === 'ops.restic.interval_minutes')!;
    expect(child.kind).toBe('integer');
    expect(child.min).toBe(15);
    expect(child.unit).toBe('分钟');
    const remote = ops.fields.find((f) => f.path === 'ops.restic.remote')!;
    expect(remote.nullable).toBe(true);
    expect(remote.advanced).toBe(true);
  });

  it('marks the lines widget on a string array', () => {
    const ops = built[1];
    const admins = ops.fields.find((f) => f.path === 'ops.admins')!;
    expect(admins.kind).toBe('string-array');
    expect(admins.widget).toBe('lines');
  });

  it('propagates required from the containing object', () => {
    const server = built[0];
    expect(server.fields.find((f) => f.path === 'server.port')!.required).toBe(true);
    expect(server.fields.find((f) => f.path === 'server.spawn')!.required).toBe(false);
  });
});

describe('memory widget (-Xms/-Xmx)', () => {
  it('parses JVM size tokens to GB', () => {
    expect(parseJvmSizeGb('8G')).toBe(8);
    expect(parseJvmSizeGb('8g')).toBe(8);
    expect(parseJvmSizeGb('8192M')).toBe(8);
    expect(parseJvmSizeGb('1T')).toBe(1024);
    expect(parseJvmSizeGb('nope')).toBeNull();
  });

  it('reads -Xmx (preferred) then -Xms', () => {
    expect(readMemoryGb(['-Xms4G', '-Xmx10G', '-XX:+UseG1GC'])).toBe(10);
    expect(readMemoryGb(['-Xms6G'])).toBe(6);
    expect(readMemoryGb(['-XX:+UseG1GC'])).toBeNull();
  });

  it('writes both -Xms/-Xmx and keeps the other opts, in place', () => {
    const out = writeMemoryGb(['-Xms8G', '-XX:+UseG1GC', '-Xmx8G'], 12);
    expect(out).toEqual(['-Xms12G', '-XX:+UseG1GC', '-Xmx12G']);
    expect(otherJvmOpts(out)).toEqual(['-XX:+UseG1GC']);
  });

  it('appends -Xms/-Xmx when absent, uses MB for a fractional GB', () => {
    expect(writeMemoryGb(['-XX:+UseG1GC'], 6)).toEqual(['-XX:+UseG1GC', '-Xms6G', '-Xmx6G']);
    expect(writeMemoryGb([], 1.5)).toEqual(['-Xms1536M', '-Xmx1536M']);
  });
});

describe('getAtPath / setAtPath', () => {
  it('reads and writes nested leaves immutably', () => {
    const v = { a: { b: { c: 1 } } };
    expect(getAtPath(v, 'a.b.c')).toBe(1);
    const next = setAtPath(v, 'a.b.c', 2);
    expect(getAtPath(next, 'a.b.c')).toBe(2);
    expect(getAtPath(v, 'a.b.c')).toBe(1); // 原对象不变
  });
});

describe('validateField', () => {
  const port = { path: 'server.port', key: 'port', label: '端口', kind: 'integer', advanced: false, nullable: false, required: true, min: 1, max: 65535 } as const;
  it('rejects out-of-range and non-integer', () => {
    expect(validateField(port, 70000)).toMatch(/不能大于/);
    expect(validateField(port, 0)).toMatch(/不能小于/);
    expect(validateField(port, 1.5)).toMatch(/整数/);
    expect(validateField(port, 25565)).toBeNull();
  });
  it('flags required empty', () => {
    expect(validateField(port, '')).toMatch(/必填/);
  });
});

describe('changedPaths', () => {
  it('returns only leaves that differ', () => {
    const fields = buildSections(schema, sections)[0].fields;
    const original = { server: { java_opts: ['-Xmx8G'], port: 25565, spawn: [0, 64, 0] } };
    const draft = { server: { java_opts: ['-Xmx8G'], port: 25566, spawn: [0, 64, 0] } };
    expect(changedPaths(fields, original, draft)).toEqual(['server.port']);
  });
});

describe('deepEqual', () => {
  it('compares arrays and objects structurally', () => {
    expect(deepEqual([1, 2], [1, 2])).toBe(true);
    expect(deepEqual({ a: 1 }, { a: 1 })).toBe(true);
    expect(deepEqual([1, 2], [1, 3])).toBe(false);
  });
});
