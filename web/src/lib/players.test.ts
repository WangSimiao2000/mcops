import { describe, expect, it } from 'vitest';
import { playersSummary, splitPlayers } from './players';

describe('splitPlayers', () => {
  it('uses the session roster when the agent provides it', () => {
    const p = {
      online: 3,
      max: 25,
      names: ['Yuki_Y', 'Zoe', 'Sandy'],
      real: ['Yuki_Y'],
      fake: ['Zoe', 'Sandy'],
      real_count: 1,
      fake_count: 2,
    };
    expect(splitPlayers(p)).toEqual({
      real: ['Yuki_Y'],
      fake: ['Zoe', 'Sandy'],
      realCount: 1,
      fakeCount: 2,
    });
    expect(playersSummary(p)).toBe('3/25（真人 1 · 假人 2）');
  });

  it('falls back to the server-list sample and hides Anonymous Player', () => {
    const p = { online: 2, max: 25, names: ['Anonymous Player', 'Alice'] };
    expect(splitPlayers(p).real).toEqual(['Alice']);
    expect(playersSummary(p)).toBe('2/25');
  });
});
