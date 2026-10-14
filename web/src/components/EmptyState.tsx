import type { ReactNode } from 'react';

/**
 * 空状态：淡图标 + 一行引导。用于「有对应动作」的空表（如备份空态指向「立即备份」）。
 * 默认是一个收件箱图标；传 icon 覆盖。
 */
export function EmptyState({ hint, icon }: { hint: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-2 text-slate-400 dark:text-slate-500">
      <span aria-hidden className="text-slate-300 dark:text-slate-600">
        {icon ?? <InboxIcon />}
      </span>
      <p className="text-sm">{hint}</p>
    </div>
  );
}

function InboxIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-7 w-7" aria-hidden="true">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3.5 12.5 5.3 5.6A2 2 0 0 1 7.2 4h9.6a2 2 0 0 1 1.9 1.6l1.8 6.9M3.5 12.5V18a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-5.5M3.5 12.5H8l1.5 2.2h5L16 12.5h4.5"
      />
    </svg>
  );
}
