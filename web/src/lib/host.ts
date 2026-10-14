import { formatBytes } from '@/lib/ops';

/** 0–100 的占用率 → 颜色等级 */
export function usageTone(pct: number | null | undefined): 'ok' | 'warn' | 'bad' {
  if (pct == null) return 'ok';
  if (pct >= 90) return 'bad';
  if (pct >= 75) return 'warn';
  return 'ok';
}

export function formatRate(bps: number | null | undefined): string {
  if (bps == null) return '—';
  return `${formatBytes(bps)}/s`;
}

export function formatUptimeShort(s: number | null | undefined): string {
  if (s == null) return '—';
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d} 天 ${h} 小时` : h > 0 ? `${h} 小时 ${m} 分` : `${m} 分`;
}
