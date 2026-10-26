/** 面板「世界地图」卡片用的小工具 */
import type { MapRun } from '@/api/types';

const TRIGGER: Record<string, string> = { restart: '每日重启', schedule: '定时', test: '测试' };

export function triggerLabel(t: string): string {
  if (t.startsWith('web:')) return `面板（${t.slice(4)}）`;
  return TRIGGER[t] ?? t;
}

export function durationLabel(sec: number | undefined): string {
  if (sec === undefined) return '—';
  return sec < 90 ? `${sec} 秒` : `${Math.round(sec / 60)} 分钟`;
}

/** 这次有多少区域变了（全图那一级重画的区域数） */
export function changedRegions(run: MapRun | null | undefined): number | null {
  const r = run?.result?.render?.rendered;
  if (!r) return null;
  return Number(r['1'] ?? 0);
}
