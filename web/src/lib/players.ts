import type { PlayersInfo } from '@/api/types';

const ANONYMOUS = 'Anonymous Player';

export interface PlayerSplit {
  real: string[];
  fake: string[];
  realCount: number;
  fakeCount: number;
}

/** Split online players into real and fake (carpet). Falls back to the server-list sample. */
export function splitPlayers(p: PlayersInfo): PlayerSplit {
  if (p.real && p.fake) {
    return {
      real: p.real,
      fake: p.fake,
      realCount: p.real_count ?? Math.max(p.online - p.fake.length, 0),
      fakeCount: p.fake_count ?? p.fake.length,
    };
  }
  const real = p.names.filter((n) => n && n !== ANONYMOUS);
  return { real, fake: [], realCount: p.online, fakeCount: 0 };
}

/** "3/25" or "3/25（真人 1 · 假人 2）" */
export function playersSummary(p: PlayersInfo): string {
  const s = splitPlayers(p);
  const base = `${p.online}/${p.max}`;
  return s.fakeCount > 0 ? `${base}（真人 ${s.realCount} · 假人 ${s.fakeCount}）` : base;
}
