/**
 * Exponential backoff with full jitter, capped at `maxMs`.
 *
 * attempt 0 -> no prior failure (should not normally be called)
 * attempt 1 -> ~baseMs
 * attempt n -> min(baseMs * 2^(n-1), maxMs), then jittered into [half, full].
 *
 * `rand` is injectable for deterministic tests (defaults to Math.random).
 */
export function backoffDelay(
  attempt: number,
  baseMs = 1000,
  maxMs = 30_000,
  rand: () => number = Math.random,
): number {
  if (attempt <= 0) return 0;
  const exp = baseMs * 2 ** (attempt - 1);
  const capped = Math.min(exp, maxMs);
  // Full jitter in [capped/2, capped] to avoid thundering herd while
  // keeping a sensible minimum wait.
  const half = capped / 2;
  return Math.round(half + rand() * half);
}
