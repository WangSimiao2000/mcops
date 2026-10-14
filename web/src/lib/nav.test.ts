import { describe, expect, it } from 'vitest';
import { filterNavSections, NAV_ITEMS, NAV_SECTIONS, pageTitle } from './nav';

describe('NAV_SECTIONS', () => {
  it('groups every nav item under exactly one labelled section', () => {
    expect(NAV_SECTIONS.map((s) => s.heading)).toEqual(['运行', '安全', '集成与设置']);
    const flat = NAV_SECTIONS.flatMap((s) => s.items.map((i) => i.to));
    // No item appears in two sections.
    expect(new Set(flat).size).toBe(flat.length);
    // NAV_ITEMS is the flattened view of the sections.
    expect(NAV_ITEMS.map((i) => i.to)).toEqual(flat);
  });

  it('places each page in the expected section', () => {
    const by = (heading: string) =>
      NAV_SECTIONS.find((s) => s.heading === heading)!.items.map((i) => i.to);
    expect(by('运行')).toEqual(['/', '/console', '/ops', '/backups', '/mods']);
    expect(by('安全')).toEqual(['/security', '/network', '/players', '/whitelist']);
    expect(by('集成与设置')).toEqual(['/bot', '/logs', '/settings']);
  });

  it('covers all twelve routes once', () => {
    expect(NAV_ITEMS).toHaveLength(12);
  });
});

describe('pageTitle', () => {
  it('names each page so browser tabs can be told apart', () => {
    expect(pageTitle('/')).toBe('总览 · mc-ops');
    expect(pageTitle('/network')).toBe('网络 · mc-ops');
    expect(pageTitle('/network/')).toBe('网络 · mc-ops');
    expect(pageTitle('/players/Yuki_Y')).toBe('Yuki_Y · 玩家 · mc-ops');
    expect(pageTitle('/ips/1.2.3.4')).toBe('1.2.3.4 · IP · mc-ops');
    expect(pageTitle('/players/%E5%90%8D')).toBe('名 · 玩家 · mc-ops');
    expect(pageTitle('/players/%E5')).toBe('%E5 · 玩家 · mc-ops');
    expect(pageTitle('/nope')).toBe('mc-ops');
  });
});

describe('filterNavSections', () => {
  it('shows everything while capabilities are unknown (undefined)', () => {
    const out = filterNavSections(undefined);
    expect(out.flatMap((s) => s.items.map((i) => i.to))).toContain('/bot');
  });

  it('shows everything when bot is enabled', () => {
    const out = filterNavSections({ features: { bot: true, edge: true } });
    expect(out.flatMap((s) => s.items.map((i) => i.to))).toContain('/bot');
  });

  it('hides the QQ 机器人 nav item when features.bot=false', () => {
    const out = filterNavSections({ features: { bot: false, edge: true } });
    const paths = out.flatMap((s) => s.items.map((i) => i.to));
    expect(paths).not.toContain('/bot');
    // other items untouched
    expect(paths).toContain('/settings');
    expect(paths).toContain('/logs');
  });
});
