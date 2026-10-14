import type { IpTag, NetWindow } from '@/api/network';
import type { BadgeTone } from '@/components/ui/Badge';

export const WINDOWS: NetWindow[] = ['1h', '24h', '7d', '30d'];

export const WINDOW_LABEL: Record<NetWindow, string> = {
  '1h': '1 小时',
  '24h': '24 小时',
  '7d': '7 天',
  '30d': '30 天',
};

export const TAG_LABEL: Record<IpTag, string> = {
  player: '玩家',
  scanner: '扫描器',
  ssh: 'SSH',
  banned: '已封禁',
};

export const TAG_TONE: Record<IpTag, BadgeTone> = {
  player: 'green',
  scanner: 'yellow',
  ssh: 'red',
  banned: 'gray',
};

export const PROXY_COLOR: Record<string, string> = {
  minecraft: '#16a34a',
  ssh: '#dc2626',
  frp: '#2563eb',
};

/**
 * 柱体/图例的主题感知填充类（暗色下自动变亮），替代硬编码 hex。
 * 未知 proxy 回退到中性 slate。
 */
export const PROXY_FILL_CLASS: Record<string, string> = {
  minecraft: 'fill-green-600 dark:fill-green-500',
  ssh: 'fill-red-600 dark:fill-red-500',
  frp: 'fill-blue-600 dark:fill-blue-500',
};

export const PROXY_FILL_FALLBACK = 'fill-slate-500 dark:fill-slate-400';

/** 图例色块（text-current + bg-current 由容器文字色驱动）用的文字类。 */
export const PROXY_TEXT_CLASS: Record<string, string> = {
  minecraft: 'text-green-600 dark:text-green-500',
  ssh: 'text-red-600 dark:text-red-500',
  frp: 'text-blue-600 dark:text-blue-500',
};

export const PROXY_TEXT_FALLBACK = 'text-slate-500 dark:text-slate-400';

// ---- 链路拓扑 --------------------------------------------------------------
export type Health = 'ok' | 'warn' | 'bad';

export function dotColor(h: Health): string {
  return h === 'ok' ? 'bg-green-500' : h === 'warn' ? 'bg-yellow-500' : 'bg-red-500';
}

/** 横轴时区说明：后端按服务器本地时区分桶，+480 即北京时间。无 offset 时返回空串（不误标 UTC）。 */
export function tzLabel(offsetMin: number | undefined): string {
  if (offsetMin === undefined) return '';
  if (offsetMin === 480) return '时间：北京时间（UTC+8）';
  const sign = offsetMin >= 0 ? '+' : '-';
  const abs = Math.abs(offsetMin);
  const hm = abs % 60 === 0 ? `${abs / 60}` : `${Math.floor(abs / 60)}:${String(abs % 60).padStart(2, '0')}`;
  return `时间：UTC${sign}${hm}`;
}
