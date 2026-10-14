import { describe, expect, it } from 'vitest';
import type { Capabilities } from '@/api/config';
import { featureOn, modOn } from '@/lib/capabilities';

const caps = (
  over: Partial<Capabilities['features']> = {},
  mods: Partial<Capabilities['mods']> = {},
): Capabilities => ({
  mc_version: '1.21.11',
  loader: 'fabric',
  loader_version: '0.19.2',
  mods: { easyauth: true, carpet: true, proxy_protocol: true, minimotd: true, ...mods },
  proxy_source: '10.88.88.1',
  features: { edge: true, bot: true, restic: true, systemd: true, flight_watch: true, ...over },
});

describe('featureOn / modOn default-to-show', () => {
  it('returns true when caps is undefined (loading / error)', () => {
    expect(featureOn(undefined, 'bot')).toBe(true);
    expect(modOn(undefined, 'easyauth')).toBe(true);
  });
  it('reflects explicit false', () => {
    expect(featureOn(caps({ bot: false }), 'bot')).toBe(false);
    expect(featureOn(caps({ edge: false }), 'edge')).toBe(false);
    expect(modOn(caps({}, { easyauth: false }), 'easyauth')).toBe(false);
  });
  it('reflects explicit true', () => {
    expect(featureOn(caps(), 'bot')).toBe(true);
    expect(modOn(caps(), 'easyauth')).toBe(true);
  });
});
