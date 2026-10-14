import { describe, expect, it } from 'vitest';
import { backoffDelay } from '@/api/backoff';

describe('backoffDelay', () => {
  it('returns 0 for attempt <= 0', () => {
    expect(backoffDelay(0)).toBe(0);
    expect(backoffDelay(-3)).toBe(0);
  });

  it('grows exponentially with the base before jitter', () => {
    // rand=1 => full of the capped value
    expect(backoffDelay(1, 1000, 30_000, () => 1)).toBe(1000);
    expect(backoffDelay(2, 1000, 30_000, () => 1)).toBe(2000);
    expect(backoffDelay(3, 1000, 30_000, () => 1)).toBe(4000);
    expect(backoffDelay(5, 1000, 30_000, () => 1)).toBe(16_000);
  });

  it('caps at maxMs', () => {
    // attempt 6 => 32000 raw, capped to 30000
    expect(backoffDelay(6, 1000, 30_000, () => 1)).toBe(30_000);
    expect(backoffDelay(20, 1000, 30_000, () => 1)).toBe(30_000);
  });

  it('applies full jitter in [capped/2, capped]', () => {
    // rand=0 => lower bound (half)
    expect(backoffDelay(3, 1000, 30_000, () => 0)).toBe(2000);
    // rand=0.5 => three quarters
    expect(backoffDelay(3, 1000, 30_000, () => 0.5)).toBe(3000);
  });

  it('never exceeds the cap even with jitter at the ceiling', () => {
    for (let a = 1; a <= 15; a += 1) {
      const d = backoffDelay(a, 1000, 30_000, () => 0.999);
      expect(d).toBeLessThanOrEqual(30_000);
    }
  });
});
