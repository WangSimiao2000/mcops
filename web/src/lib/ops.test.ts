import { describe, expect, it } from 'vitest';
import {
  estimateDownload,
  formatBytes,
  formatCountdown,
  jobDisplayName,
  JOB_NAME,
  JOB_RUN_STATUS_LABEL,
  jobRunStatusTone,
  totalBackupSize,
} from '@/lib/ops';
import type { Backup } from '@/api/types';

describe('jobDisplayName', () => {
  it('maps known job ids to Chinese names', () => {
    expect(jobDisplayName('scheduled_restart')).toBe('定时重启 + 冷备份');
    expect(jobDisplayName('backup')).toBe('立即备份（不停服）');
    expect(jobDisplayName('full_backup')).toBe('完整备份');
    expect(jobDisplayName('restic_backup')).toBe('增量快照');
    expect(jobDisplayName('restic')).toBe('增量快照');
  });

  it('falls back to the raw id for unknown jobs', () => {
    expect(jobDisplayName('something_else')).toBe('something_else');
    expect(jobDisplayName('')).toBe('');
  });

  it('JOB_NAME covers the known ids', () => {
    expect(Object.keys(JOB_NAME)).toContain('scheduled_restart');
    expect(Object.keys(JOB_NAME)).toContain('restic_backup');
  });
});

describe('formatBytes', () => {
  it('bytes under 1 KiB stay in B', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1023)).toBe('1023 B');
  });
  it('scales through KB/MB/GB', () => {
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(1024 * 1024)).toBe('1 MB');
    expect(formatBytes(486539264)).toBe('464 MB');
    expect(formatBytes(1288490188)).toBe('1.2 GB');
  });
  it('drops trailing .0', () => {
    expect(formatBytes(2 * 1024 * 1024)).toBe('2 MB');
  });
  it('invalid/negative -> em dash', () => {
    expect(formatBytes(-1)).toBe('—');
    expect(formatBytes(NaN)).toBe('—');
  });
});

describe('totalBackupSize', () => {
  const b = (size: number): Backup => ({ name: 'x', size, mtime: '2026-10-09T00:00:00Z' });
  it('sums sizes', () => {
    expect(totalBackupSize([b(100), b(200), b(50)])).toBe(350);
  });
  it('empty -> 0', () => {
    expect(totalBackupSize([])).toBe(0);
  });
  it('ignores negative/NaN entries', () => {
    expect(totalBackupSize([b(100), b(-5), b(NaN)])).toBe(100);
  });
});

describe('jobRunStatusTone / labels', () => {
  it('maps status to tone', () => {
    expect(jobRunStatusTone('ok')).toBe('green');
    expect(jobRunStatusTone('failed')).toBe('red');
    expect(jobRunStatusTone('running')).toBe('yellow');
  });
  it('has zh labels', () => {
    expect(JOB_RUN_STATUS_LABEL.ok).toBe('成功');
    expect(JOB_RUN_STATUS_LABEL.failed).toBe('失败');
    expect(JOB_RUN_STATUS_LABEL.running).toBe('运行中');
  });
});

describe('formatCountdown', () => {
  const now = Date.parse('2026-10-09T07:00:00Z');
  it('null/invalid -> null', () => {
    expect(formatCountdown(null, now)).toBeNull();
    expect(formatCountdown('not-a-date', now)).toBeNull();
  });
  it('past target -> 已过期', () => {
    expect(formatCountdown('2026-10-09T06:00:00Z', now)).toBe('已过期');
    expect(formatCountdown('2026-10-09T07:00:00Z', now)).toBe('已过期');
  });
  it('days + hours', () => {
    expect(formatCountdown('2026-10-10T10:00:00Z', now)).toBe('1天3小时');
  });
  it('hours + minutes', () => {
    expect(formatCountdown('2026-10-09T09:30:00Z', now)).toBe('2小时30分');
  });
  it('minutes + seconds', () => {
    expect(formatCountdown('2026-10-09T07:05:30Z', now)).toBe('5分30秒');
  });
  it('seconds only', () => {
    expect(formatCountdown('2026-10-09T07:00:45Z', now)).toBe('45秒');
  });
});

describe('estimateDownload', () => {
  it('formats minutes and hours, null when unthrottled', () => {
    expect(estimateDownload(10 * 1024, 512)).toBe('约 1 分钟');
    expect(estimateDownload(3.1 * 1024 ** 3, 512)).toBe('约 1 小时 46 分');
    expect(estimateDownload(512 * 1024 * 3600, 512)).toBe('约 1 小时');
    expect(estimateDownload(1, 0)).toBeNull();
  });
});

describe('formatSeconds', () => {
  it('formats compact Chinese durations', async () => {
    const { formatSeconds } = await import('@/lib/ops');
    expect(formatSeconds(45)).toBe('45 秒');
    expect(formatSeconds(200)).toBe('3 分 20 秒');
    expect(formatSeconds(3900)).toBe('1 小时 5 分');
    expect(formatSeconds(7200)).toBe('2 小时');
    expect(formatSeconds(null)).toBeNull();
    expect(formatSeconds(-1)).toBeNull();
  });
});
