import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { ConfigHistoryItem } from '@/api/config';
import { ApiError } from '@/api/http';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { formatDateTime } from '@/lib/format';
import { scrollBox } from '@/lib/scroll';

export function HistoryTab() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ['configHistory'],
    queryFn: ({ signal }) => api.configHistory(50, signal),
    retry: false,
  });
  const [pending, setPending] = useState<ConfigHistoryItem | null>(null);
  const [error, setError] = useState<string | null>(null);

  const restoreMut = useMutation({
    mutationFn: (id: number) => api.restoreConfigHistory(id),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ['config'] });
      void qc.invalidateQueries({ queryKey: ['configHistory'] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.detail : '恢复失败'),
  });

  if (query.isError) {
    const detail = query.error instanceof ApiError ? query.error.detail : '无法加载修改历史';
    return (
      <Card>
        <CardBody>
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {detail}
          </p>
        </CardBody>
      </Card>
    );
  }
  if (!query.data) return <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>;

  const items = query.data.items;

  return (
    <div className="space-y-4">
      {error ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}

      <Card>
        <CardBody className="p-0">
          {items.length === 0 ? (
            <p className="p-4 text-sm text-slate-500 dark:text-slate-400">暂无修改记录。</p>
          ) : (
            <ul className={scrollBox('lg', 'divide-y divide-slate-100 dark:divide-slate-800')} tabIndex={0}>
              {items.map((h) => (
                <li key={h.id} className="flex flex-wrap items-start gap-x-4 gap-y-2 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium text-slate-800 dark:text-slate-200">#{h.id}</span>
                      <span className="text-slate-500 dark:text-slate-400">{formatDateTime(h.ts)}</span>
                      <span className="text-slate-500 dark:text-slate-400">{h.user}</span>
                    </div>
                    {h.note ? (
                      <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">{h.note}</p>
                    ) : null}
                    <div className="mt-1 flex flex-wrap gap-1">
                      {h.changed.map((p) => (
                        <code
                          key={p}
                          className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                        >
                          {p}
                        </code>
                      ))}
                    </div>
                  </div>
                  <Button size="sm" variant="secondary" onClick={() => setPending(h)}>
                    恢复到此版本
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <ConfirmDialog
        open={pending !== null}
        title="恢复到此版本"
        description={
          pending
            ? `将把配置覆盖层恢复为版本 #${pending.id}（${formatDateTime(pending.ts)}）保存后的状态。agent 类改动需重启 agent 生效。`
            : ''
        }
        confirmLabel="恢复"
        onConfirm={() => {
          if (pending) restoreMut.mutate(pending.id);
          setPending(null);
        }}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}
