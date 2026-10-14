// Pure logic for the M1 players/security pages. No React, fully unit-tested.

import type { BadgeTone } from '@/components/ui/Badge';
import type {
  AlertSeverity,
  OpenCounts,
  Player,
  SystemMode,
} from '@/api/types';

/** Flags considered high-risk (sort such players to the top of the list). */
export const HIGH_RISK_FLAGS = new Set<string>(['unregistered_whitelisted']);

export function isHighRisk(player: Player): boolean {
  return player.flags.some((f) => HIGH_RISK_FLAGS.has(f));
}

/** Epoch millis for sorting; null/invalid timestamps sort last (as -Infinity). */
function lastSeenMs(player: Player): number {
  if (!player.last_seen) return Number.NEGATIVE_INFINITY;
  const t = new Date(player.last_seen).getTime();
  return Number.isNaN(t) ? Number.NEGATIVE_INFINITY : t;
}

/**
 * Default player ordering: high-risk first, then most-recently-seen first.
 * Returns a NEW array; does not mutate the input.
 */
export function sortPlayers(players: Player[]): Player[] {
  return [...players].sort((a, b) => {
    const ra = isHighRisk(a) ? 1 : 0;
    const rb = isHighRisk(b) ? 1 : 0;
    if (ra !== rb) return rb - ra; // risky first
    return lastSeenMs(b) - lastSeenMs(a); // newest last_seen first
  });
}

/** Severity -> badge tone used across the bell and the security page. */
export function severityTone(severity: AlertSeverity): BadgeTone {
  switch (severity) {
    case 'critical':
      return 'red';
    case 'warning':
      return 'yellow';
    case 'info':
      return 'blue';
  }
}

export const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  critical: '严重',
  warning: '警告',
  info: '提示',
};

export const ALERT_STATUS_LABEL = {
  open: '未处理',
  acked: '已确认',
} as const;

/** Total unacked alerts across all severities. */
export function totalOpen(counts: OpenCounts): number {
  return counts.critical + counts.warning + counts.info;
}

/**
 * Bell icon tone from open counts: red if any critical, yellow if any warning,
 * blue if only info, gray if nothing open. Drives the topbar badge color.
 */
export function bellTone(counts: OpenCounts): BadgeTone {
  if (counts.critical > 0) return 'red';
  if (counts.warning > 0) return 'yellow';
  if (counts.info > 0) return 'blue';
  return 'gray';
}

/** Whether write controls (start/stop/restart, command input) are disabled. */
export function isControlDisabled(mode: SystemMode | undefined): boolean {
  // Default to disabled (safe) until we know we are in control mode.
  return mode !== 'control';
}

export const OBSERVE_NOTICE = '观测模式：服务器仍由旧脚本管理';

/** Known-flag -> short zh label + optional tooltip for the players table. */
export interface FlagMeta {
  label: string;
  tone: BadgeTone;
  title?: string;
}

export const FLAG_META: Record<string, FlagMeta> = {
  unregistered_whitelisted: {
    label: '未注册',
    tone: 'red',
    title: '白名单内但 EasyAuth 未注册，任何人可抢注冒用',
  },
  night_owl: { label: '凌晨活跃', tone: 'yellow' },
  new_account: { label: '新账号', tone: 'yellow' },
};

export function flagMeta(flag: string): FlagMeta {
  return FLAG_META[flag] ?? { label: flag, tone: 'gray' };
}
