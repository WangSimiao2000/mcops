import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/http';
import {
  mockConfig,
  mockProperties,
  mockResetConfig,
  mockRestartAgent,
  mockUpdateConfig,
  mockUpdateProperties,
} from '@/api/configMock';

describe('mock config backend', () => {
  it('returns schema, sections, values, overridden and pending', () => {
    const r = mockConfig();
    expect(r.schema.properties).toBeTruthy();
    expect(r.sections.map((s) => s.key)).toContain('server');
    expect(r.overridden.length).toBeGreaterThan(0);
    expect(getLeaf(r.values, 'server.port')).toBe(25565);
  });

  it('rejects an out-of-range port with 422', () => {
    expect(() => mockUpdateConfig({ server: { port: 70000 } })).toThrow(ApiError);
  });

  it('records an agent-type change into pending and clears it on restart', () => {
    mockUpdateConfig({ ops: { restart_time: '06:00' } }, 'push restart later');
    const after = mockConfig();
    expect(after.pending.some((p) => p.path === 'ops.restart_time' && p.apply === 'agent')).toBe(true);
    mockRestartAgent();
    const restarted = mockConfig();
    expect(restarted.pending.some((p) => p.path === 'ops.restart_time')).toBe(false);
  });

  it('reset returns a path to its yaml default and removes the override', () => {
    mockUpdateConfig({ server: { port: 25570 } });
    expect(getLeaf(mockConfig().values, 'server.port')).toBe(25570);
    const res = mockResetConfig(['server.port']);
    expect(res.overridden).not.toContain('server.port');
    expect(getLeaf(res.values, 'server.port')).toBe(25565);
  });
});

describe('mock server.properties', () => {
  it('never exposes a sensitive value and marks managed keys read-only', () => {
    const r = mockProperties();
    const rcon = r.items.find((i) => i.key === 'rcon.password')!;
    expect(rcon.sensitive).toBe(true);
    expect(rcon.value).toBeNull();
    expect(rcon.managed).toBe(true);
    expect(r.items.find((i) => i.key === 'server-port')!.managed).toBe(true);
  });

  it('saves a diff and returns a backup filename', () => {
    const res = mockUpdateProperties({ difficulty: 'hard' });
    expect(res.changed).toEqual(['difficulty']);
    expect(res.backup).toMatch(/^server\.properties\.bak-\d{8}-\d{6}/);
    expect(mockProperties().items.find((i) => i.key === 'difficulty')!.value).toBe('hard');
  });

  it('rejects editing a managed key', () => {
    expect(() => mockUpdateProperties({ 'server-port': 25599 })).toThrow(ApiError);
  });

  it('rejects an int out of its range', () => {
    expect(() => mockUpdateProperties({ 'view-distance': 99 })).toThrow(ApiError);
  });
});

function getLeaf(obj: Record<string, unknown>, path: string): unknown {
  let cur: unknown = obj;
  for (const p of path.split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}
