import type { ReactNode } from 'react';

/**
 * 统一页面标题区：所有页面用它替代各自手写的 <h1>，保证桌面/移动端顶部节奏一致。
 * - title：主标题（渲染为 h1）
 * - subtitle：可选副标题，一行说明
 * - actions：可选右侧动作区（如「保存」「全部更新」）
 */
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 pt-0.5">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold leading-tight text-slate-900 dark:text-slate-100">
          {title}
        </h1>
        {subtitle ? (
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2 sm:gap-3">{actions}</div> : null}
    </div>
  );
}
