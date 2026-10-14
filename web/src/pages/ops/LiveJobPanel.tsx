import type { LiveJob } from '@/api/types';
import { formatBytes, formatSeconds } from '@/lib/ops';
import { scrollBox } from '@/lib/scroll';

/**
 * 正在运行的任务：当前步骤的进度条（有字节进度时）+ 实时日志。
 * 运行记录要等任务结束才落库，所以长任务（回退、升级）期间靠它看进行到哪了。
 */
export function LiveJobPanel({ live, name }: { live: LiveJob; name: string }) {
  const p = live.progress;
  const lines = live.log ? live.log.split('\n') : [];
  const last = lines[lines.length - 1] ?? '';
  const eta = formatSeconds(p?.eta_s);

  return (
    <section
      aria-label={`正在运行：${name}`}
      className="space-y-2 rounded-md border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-900 dark:border-yellow-900/50 dark:bg-yellow-900/20 dark:text-yellow-200"
    >
      <p role="status">
        任务「{name}」正在运行
        {last ? <span className="text-yellow-800 dark:text-yellow-300">：{last.replace(/^\d\d:\d\d:\d\d /, '')}</span> : null}
      </p>

      {p ? (
        <div className="space-y-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs">
            <span className="font-medium">{p.label}</span>
            <span className="tabular-nums">
              {p.percent != null ? `${p.percent.toFixed(1)}%` : '准备中…'}
              {p.total_bytes > 0
                ? ` · ${formatBytes(p.done_bytes)} / ${formatBytes(p.total_bytes)}`
                : null}
            </span>
          </div>
          <div
            role="progressbar"
            aria-label={p.label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={p.percent ?? undefined}
            className="h-2 overflow-hidden rounded bg-yellow-200/70 dark:bg-yellow-900/50"
          >
            <div
              className="h-full rounded bg-yellow-500 transition-[width] duration-500 motion-reduce:transition-none dark:bg-yellow-400"
              style={{ width: `${Math.min(p.percent ?? 0, 100)}%` }}
            />
          </div>
          <p className="text-xs tabular-nums text-yellow-800 dark:text-yellow-300">
            {p.rate_bps != null ? `${formatBytes(p.rate_bps)}/s` : '测速中'}
            {' · '}
            {eta ? `预计还要 ${eta}` : '剩余时间估算中'}
            {' · '}已用 {formatSeconds(p.elapsed_s) ?? '0 秒'}
          </p>
        </div>
      ) : null}

      {lines.length > 1 ? (
        <details>
          <summary className="cursor-pointer text-xs">实时日志（{lines.length} 行）</summary>
          <pre
            className={scrollBox(
              'sm',
              'mt-1 whitespace-pre-wrap rounded bg-white/70 p-2 text-xs text-slate-700 dark:bg-slate-900/60 dark:text-slate-200',
            )}
            tabIndex={0}
            aria-label="实时日志"
          >
            {live.log}
          </pre>
        </details>
      ) : null}
      <p className="text-xs text-yellow-700 dark:text-yellow-400">页面每 3 秒自动刷新</p>
    </section>
  );
}
