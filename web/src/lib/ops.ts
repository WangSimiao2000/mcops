// Pure logic for the M3 运维任务 / 备份 pages. No React, fully unit-tested.

import type { BadgeTone } from '@/components/ui/Badge';
import type { Backup, JobRunStatus } from '@/api/types';

export const JOB_RUN_STATUS_LABEL: Record<JobRunStatus, string> = {
  running: '运行中',
  ok: '成功',
  failed: '失败',
  interrupted: '已中断',
};

/**
 * 任务 id -> 中文名。后端用 id（scheduled_restart 等）标识任务，界面统一展示中文名，
 * 原始 id 作为 title tooltip 保留以便排查。未知 id 回退为原文。
 */
export const JOB_NAME: Record<string, string> = {
  scheduled_restart: '定时重启 + 冷备份',
  backup: '立即备份（不停服）',
  full_backup: '完整备份',
  restic_backup: '增量快照',
  restic: '增量快照',
  offsite_copy: '异地备份',
  snapshot_restore: '从快照回退',
  game_prepare: '准备游戏升级',
  game_upgrade: '游戏版本升级',
  setup_install: '安装服务端',
};

/** 任务 id 的中文显示名；未知 id 原样返回。 */
export function jobDisplayName(id: string): string {
  return JOB_NAME[id] ?? id;
}

export function jobRunStatusTone(status: JobRunStatus): BadgeTone {
  switch (status) {
    case 'ok':
      return 'green';
    case 'failed':
      return 'red';
    case 'running':
      return 'yellow';
    case 'interrupted':
      return 'gray';
  }
}

/**
 * Human-readable byte size (zh-CN style, binary units). Negative/NaN -> '—'.
 * 1536 -> "1.5 KB"; 0 -> "0 B".
 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  // 1 位小数，去掉末尾的 .0。
  const rounded = Math.round(value * 10) / 10;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${text} ${units[i]}`;
}

/** 秒数 -> 「1 小时 5 分」/「3 分 20 秒」/「45 秒」；null/负数 -> null。 */
export function formatSeconds(s: number | null | undefined): string | null {
  if (s == null || !Number.isFinite(s) || s < 0) return null;
  const t = Math.round(s);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  if (h > 0) return m > 0 ? `${h} 小时 ${m} 分` : `${h} 小时`;
  if (m > 0) return sec > 0 ? `${m} 分 ${sec} 秒` : `${m} 分`;
  return `${sec} 秒`;
}

/** 合计多份备份的总字节数。 */
export function totalBackupSize(items: Backup[]): number {
  return items.reduce((sum, b) => sum + (Number.isFinite(b.size) && b.size > 0 ? b.size : 0), 0);
}

/**
 * 到 `targetIso` 的倒计时，相对 `now`（毫秒）。
 * - null/非法时间 -> null
 * - 已过期 -> "已过期"
 * - 否则 "Xd Yh" / "Yh Zm" / "Zm Ws" / "Ws" 形式的紧凑中文串
 */
export function formatCountdown(targetIso: string | null, now: number = Date.now()): string | null {
  if (!targetIso) return null;
  const t = new Date(targetIso).getTime();
  if (Number.isNaN(t)) return null;
  let secs = Math.floor((t - now) / 1000);
  if (secs <= 0) return '已过期';
  const d = Math.floor(secs / 86400);
  secs -= d * 86400;
  const h = Math.floor(secs / 3600);
  secs -= h * 3600;
  const m = Math.floor(secs / 60);
  const s = secs - m * 60;
  if (d > 0) return `${d}天${h}小时`;
  if (h > 0) return `${h}小时${m}分`;
  if (m > 0) return `${m}分${s}秒`;
  return `${s}秒`;
}

/** 按限速估算下载耗时，如「约 1 小时 43 分」。rateKib=0（不限速）返回 null。 */
export function estimateDownload(bytes: number, rateKib: number): string | null {
  if (rateKib <= 0) return null;
  const minutes = Math.ceil(bytes / (rateKib * 1024) / 60);
  if (minutes < 60) return `约 ${Math.max(minutes, 1)} 分钟`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `约 ${h} 小时 ${m} 分` : `约 ${h} 小时`;
}

export function backupDownloadUrl(name: string): string {
  return `/api/backups/${encodeURIComponent(name)}/download`;
}

export function snapshotDownloadUrl(id: string): string {
  return `/api/restic/${encodeURIComponent(id)}/download`;
}
