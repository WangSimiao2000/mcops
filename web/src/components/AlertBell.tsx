import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '@/api';
import { Button } from '@/components/ui/Button';
import { useConsole } from '@/console/useConsole';
import { formatDateTime } from '@/lib/format';
import { bellTone, SEVERITY_LABEL, totalOpen } from '@/lib/security';
import { cn } from '@/lib/cn';
import { FALLBACK_REFETCH_MS } from '@/lib/live';

const DOT: Record<ReturnType<typeof bellTone>, string> = {
  red: 'bg-red-600 text-white',
  yellow: 'bg-yellow-500 text-slate-900',
  blue: 'bg-blue-600 text-white',
  gray: 'bg-slate-300 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  green: 'bg-green-600 text-white',
};

export function AlertBell() {
  const qc = useQueryClient();
  const { lastAlert } = useConsole();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const alertsQuery = useQuery({
    queryKey: ['alerts', 'open'],
    queryFn: ({ signal }) => api.alerts({ status: 'open', limit: 10 }, signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });

  // A live alert refreshes the bell immediately.
  useEffect(() => {
    if (lastAlert) {
      void qc.invalidateQueries({ queryKey: ['alerts'] });
    }
  }, [lastAlert, qc]);

  // Close the dropdown on outside click.
  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  const ackAll = useMutation({
    mutationFn: () => api.ackAllAlerts(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['alerts'] }),
  });

  const counts = alertsQuery.data?.open_counts ?? { critical: 0, warning: 0, info: 0 };
  const total = totalOpen(counts);
  const tone = bellTone(counts);
  const items = alertsQuery.data?.items ?? [];

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label={`安全告警，${total} 条未处理`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="relative flex h-8 w-8 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
      >
        <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5" aria-hidden="true">
          <path d="M10 2a1 1 0 0 1 1 1v.6a4.5 4.5 0 0 1 3.5 4.4v2.3l1.2 2.1a.8.8 0 0 1-.7 1.2H5a.8.8 0 0 1-.7-1.2l1.2-2.1V8A4.5 4.5 0 0 1 9 3.6V3a1 1 0 0 1 1-1z" />
          <path d="M8 16a2 2 0 0 0 4 0H8z" />
        </svg>
        {total > 0 ? (
          <span
            className={cn(
              'absolute -right-1 -top-1 min-w-4 rounded-full px-1 text-center text-[10px] font-bold leading-4',
              DOT[tone],
            )}
          >
            {total > 99 ? '99+' : total}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-2 w-80 rounded-lg border border-slate-200 bg-white shadow-lg dark:border-slate-800 dark:bg-slate-900"
        >
          <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2 dark:border-slate-800">
            <span className="text-sm font-semibold">
              未处理告警 {total > 0 ? `(${total})` : ''}
            </span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => ackAll.mutate()}
              disabled={ackAll.isPending || total === 0}
            >
              全部已读
            </Button>
          </div>
          <ul className="max-h-80 overflow-auto">
            {items.length === 0 ? (
              <li className="px-3 py-6 text-center text-sm text-slate-500 dark:text-slate-400">
                没有未处理告警
              </li>
            ) : (
              items.map((a) => (
                <li key={a.id} className="border-b border-slate-100 px-3 py-2 last:border-0 dark:border-slate-800">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        'inline-block h-2 w-2 shrink-0 rounded-full',
                        a.severity === 'critical'
                          ? 'bg-red-500'
                          : a.severity === 'warning'
                            ? 'bg-yellow-500'
                            : 'bg-blue-500',
                      )}
                      aria-label={SEVERITY_LABEL[a.severity]}
                    />
                    <span className="truncate text-sm font-medium">{a.title}</span>
                  </div>
                  <p className="ml-4 text-xs text-slate-400">{formatDateTime(a.last_ts)}</p>
                </li>
              ))
            )}
          </ul>
          <div className="border-t border-slate-200 px-3 py-2 text-center dark:border-slate-800">
            <Link
              to="/security"
              onClick={() => setOpen(false)}
              className="text-sm text-brand hover:underline"
            >
              查看安全页 →
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}
