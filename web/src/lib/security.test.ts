import { describe, expect, it } from 'vitest';
import {
  bellTone,
  flagMeta,
  isControlDisabled,
  isHighRisk,
  severityTone,
  sortPlayers,
  totalOpen,
} from '@/lib/security';
import type { OpenCounts, Player } from '@/api/types';

function player(partial: Partial<Player> & { name: string }): Player {
  return {
    first_seen: null,
    last_seen: null,
    sessions: 0,
    last_ip: null,
    last_ip_trusted: true,
    distinct_ips: 0,
    online: false,
    whitelisted: false,
    easyauth_registered: null,
    is_op: false,
    flags: [],
    ...partial,
  };
}

describe('isHighRisk', () => {
  it('flags unregistered_whitelisted as high-risk', () => {
    expect(isHighRisk(player({ name: 'a', flags: ['unregistered_whitelisted'] }))).toBe(true);
  });
  it('does not flag benign flags', () => {
    expect(isHighRisk(player({ name: 'b', flags: ['night_owl', 'new_account'] }))).toBe(false);
  });
  it('is false with no flags', () => {
    expect(isHighRisk(player({ name: 'c' }))).toBe(false);
  });
});

describe('sortPlayers', () => {
  it('puts high-risk players before everyone else', () => {
    const risky = player({ name: 'risky', flags: ['unregistered_whitelisted'], last_seen: '2026-10-01T00:00:00Z' });
    const recent = player({ name: 'recent', last_seen: '2026-10-09T00:00:00Z' });
    const out = sortPlayers([recent, risky]);
    expect(out[0]!.name).toBe('risky');
    expect(out[1]!.name).toBe('recent');
  });

  it('orders by last_seen descending within the same risk tier', () => {
    const older = player({ name: 'older', last_seen: '2026-10-01T00:00:00Z' });
    const newer = player({ name: 'newer', last_seen: '2026-10-09T00:00:00Z' });
    const never = player({ name: 'never', last_seen: null });
    const out = sortPlayers([older, never, newer]);
    expect(out.map((p) => p.name)).toEqual(['newer', 'older', 'never']);
  });

  it('does not mutate the input array', () => {
    const input = [player({ name: 'a' }), player({ name: 'b', flags: ['unregistered_whitelisted'] })];
    const copy = [...input];
    sortPlayers(input);
    expect(input).toEqual(copy);
  });
});

describe('severityTone', () => {
  it('maps severities to badge tones', () => {
    expect(severityTone('critical')).toBe('red');
    expect(severityTone('warning')).toBe('yellow');
    expect(severityTone('info')).toBe('blue');
  });
});

describe('bell counts & color', () => {
  const counts = (c: number, w: number, i: number): OpenCounts => ({ critical: c, warning: w, info: i });

  it('totalOpen sums all severities', () => {
    expect(totalOpen(counts(2, 3, 5))).toBe(10);
    expect(totalOpen(counts(0, 0, 0))).toBe(0);
  });

  it('bell is red when any critical open, regardless of others', () => {
    expect(bellTone(counts(1, 9, 9))).toBe('red');
  });
  it('bell is yellow when warnings but no critical', () => {
    expect(bellTone(counts(0, 1, 9))).toBe('yellow');
  });
  it('bell is blue when only info', () => {
    expect(bellTone(counts(0, 0, 3))).toBe('blue');
  });
  it('bell is gray when nothing is open', () => {
    expect(bellTone(counts(0, 0, 0))).toBe('gray');
  });
});

describe('isControlDisabled (observe mode)', () => {
  it('disables controls in observe mode', () => {
    expect(isControlDisabled('observe')).toBe(true);
  });
  it('enables controls in control mode', () => {
    expect(isControlDisabled('control')).toBe(false);
  });
  it('defaults to disabled when mode is unknown', () => {
    expect(isControlDisabled(undefined)).toBe(true);
  });
});

describe('flagMeta', () => {
  it('describes unregistered_whitelisted with a tooltip', () => {
    const m = flagMeta('unregistered_whitelisted');
    expect(m.label).toBe('未注册');
    expect(m.tone).toBe('red');
    expect(m.title).toContain('EasyAuth 未注册');
  });
  it('falls back to the raw flag name for unknown flags', () => {
    expect(flagMeta('mystery')).toEqual({ label: 'mystery', tone: 'gray' });
  });
});
