import type { BadgeTone } from '@/components/ui/Badge';
import type { ModuleState, ServerState } from '@/api/types';

export const SERVER_STATE_LABEL: Record<ServerState, string> = {
  stopped: '已停止',
  starting: '启动中',
  running: '运行中',
  stopping: '停止中',
  crashed: '已崩溃',
};

export function serverStateTone(state: ServerState): BadgeTone {
  switch (state) {
    case 'running':
      return 'green';
    case 'crashed':
      return 'red';
    case 'starting':
    case 'stopping':
      return 'yellow';
    case 'stopped':
      return 'gray';
  }
}

export const MODULE_STATE_LABEL: Record<ModuleState, string> = {
  running: '运行中',
  failed: '故障',
  stopped: '已停止',
  starting: '启动中',
};

export function moduleStateTone(state: ModuleState): BadgeTone {
  switch (state) {
    case 'running':
      return 'green';
    case 'failed':
      return 'red';
    case 'starting':
      return 'yellow';
    case 'stopped':
      return 'gray';
  }
}

/** Format a duration in seconds as a compact zh-CN string. */
export function formatUptime(seconds: number | null): string {
  if (seconds === null || seconds < 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const parts: string[] = [];
  if (d) parts.push(`${d}天`);
  if (h) parts.push(`${h}小时`);
  if (m) parts.push(`${m}分`);
  if (!d && !h) parts.push(`${s}秒`);
  return parts.join('');
}

/** Format an ISO timestamp as a local HH:MM:SS string. */
export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

/** Format an ISO timestamp as a local date+time string; '—' for null/invalid. */
export function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', { hour12: false });
}

/**
 * Duration between two ISO timestamps as a compact zh string. When `end` is
 * null (session still open) returns '进行中'. '—' if `start` is unusable.
 */
export function formatDurationBetween(start: string, end: string | null): string {
  const s = new Date(start).getTime();
  if (Number.isNaN(s)) return '—';
  if (end === null) return '进行中';
  const e = new Date(end).getTime();
  if (Number.isNaN(e) || e < s) return '—';
  return formatUptime(Math.floor((e - s) / 1000));
}
