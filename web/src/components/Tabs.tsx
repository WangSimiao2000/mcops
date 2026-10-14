import { cn } from '@/lib/cn';

export interface TabDef {
  key: string;
  label: string;
}

export function Tabs({
  tabs,
  active,
  onSelect,
  label,
}: {
  tabs: TabDef[];
  active: string;
  onSelect: (key: string) => void;
  label: string;
}) {
  return (
    <div className="border-b border-slate-200 dark:border-slate-800">
      <nav className="-mb-px flex flex-wrap gap-1" role="tablist" aria-label={label}>
        {tabs.map((t) => {
          const isActive = t.key === active;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => onSelect(t.key)}
              className={cn(
                'border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'border-brand text-brand'
                  : 'border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200',
              )}
            >
              {t.label}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
