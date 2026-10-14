import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Alert } from '@/api/types';
import { useConsole } from '@/console/useConsole';

interface Toast {
  seq: number;
  alert: Alert;
}

/**
 * Listens for critical alerts pushed over the console socket and shows a
 * dismissible toast in the top-right corner. Only `severity === 'critical'`
 * raises a toast; the bell handles the rest.
 */
export function AlertToaster() {
  const { lastAlert } = useConsole();
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    if (!lastAlert || lastAlert.alert.severity !== 'critical') return;
    setToasts((prev) =>
      prev.some((t) => t.seq === lastAlert.seq)
        ? prev
        : [...prev, { seq: lastAlert.seq, alert: lastAlert.alert }],
    );
  }, [lastAlert]);

  function dismiss(seq: number) {
    setToasts((prev) => prev.filter((t) => t.seq !== seq));
  }

  if (toasts.length === 0) return null;

  return (
    <div
      className="pointer-events-none fixed inset-x-2 top-16 z-30 flex flex-col gap-2 sm:inset-x-auto sm:right-4 sm:top-16 sm:w-80"
      role="region"
      aria-label="安全告警通知"
    >
      {toasts.map((t) => (
        <div
          key={t.seq}
          role="alert"
          className="pointer-events-auto rounded-lg border border-red-300 bg-white p-3 shadow-lg dark:border-red-800 dark:bg-slate-900"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-red-700 dark:text-red-300">
                <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4 shrink-0" aria-hidden="true">
                  <path d="M10 1.6a1 1 0 0 1 .87.5l7.7 13.3a1 1 0 0 1-.87 1.5H2.3a1 1 0 0 1-.87-1.5L9.13 2.1a1 1 0 0 1 .87-.5zm0 5.4a.9.9 0 0 0-.9.95l.25 3.5a.65.65 0 0 0 1.3 0l.25-3.5A.9.9 0 0 0 10 7zm0 6.1a.95.95 0 1 0 0 1.9.95.95 0 0 0 0-1.9z" />
                </svg>
                {t.alert.title}
              </p>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">{t.alert.detail}</p>
              <Link
                to="/security"
                onClick={() => dismiss(t.seq)}
                className="mt-1 inline-block text-xs text-brand hover:underline"
              >
                查看安全页 →
              </Link>
            </div>
            <button
              type="button"
              aria-label="关闭"
              onClick={() => dismiss(t.seq)}
              className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="h-3.5 w-3.5" aria-hidden="true">
                <path d="M5 5l10 10M15 5L5 15" />
              </svg>
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
