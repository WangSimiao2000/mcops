import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Alert, AlertStatus } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { PageHeader } from '@/components/PageHeader';
import { useConsole } from '@/console/useConsole';
import { formatDateTime } from '@/lib/format';
import {
  ALERT_STATUS_LABEL,
  SEVERITY_LABEL,
  severityTone,
} from '@/lib/security';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { scrollBox } from '@/lib/scroll';

type StatusFilter = AlertStatus | 'all';

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'open', label: '未处理' },
  { value: 'acked', label: '已确认' },
  { value: 'all', label: '全部' },
];

const SEVERITY_FILTERS: { value: '' | Alert['severity']; label: string }[] = [
  { value: '', label: '全部严重度' },
  { value: 'critical', label: '严重' },
  { value: 'warning', label: '警告' },
  { value: 'info', label: '提示' },
];

function AlertCard({ alert, onAck }: { alert: Alert; onAck: (id: string) => void; }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={severityTone(alert.severity)}>{SEVERITY_LABEL[alert.severity]}</Badge>
            <span className="font-medium">{alert.title}</span>
            {alert.count > 1 ? (
              <span className="text-xs text-slate-400">×{alert.count}</span>
            ) : null}
            {alert.status === 'acked' ? (
              <Badge tone="gray">{ALERT_STATUS_LABEL.acked}</Badge>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{alert.detail}</p>
          <p className="mt-1 text-xs text-slate-400">
            {alert.rule}
            {alert.subject ? ` · ${alert.subject}` : ''} · {formatDateTime(alert.first_ts)}
            {alert.last_ts !== alert.first_ts ? ` → ${formatDateTime(alert.last_ts)}` : ''}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          {alert.status === 'open' ? (
            <Button size="sm" variant="secondary" onClick={() => onAck(alert.id)}>
              确认
            </Button>
          ) : null}
          <button
            type="button"
            className="text-xs text-brand hover:underline"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? '收起详情' : '展开详情'}
          </button>
        </div>
      </div>
      {open ? (
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs text-slate-700 dark:bg-slate-950 dark:text-slate-300">
          {JSON.stringify(alert.data, null, 2)}
        </pre>
      ) : null}
    </li>
  );
}

function RulesCard() {
  const rules = useQuery({
    queryKey: ['securityRules'],
    queryFn: ({ signal }) => api.securityRules(signal),
  });
  return (
    <Card>
      <CardHeader title="规则（只读）" />
      <CardBody>
        {rules.isLoading ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>
        ) : (
          <ul className={scrollBox('md', 'divide-y divide-slate-100 dark:divide-slate-800')} tabIndex={0}>
            {(rules.data?.items ?? []).map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{r.name}</span>
                    <Badge tone={severityTone(r.severity)}>{SEVERITY_LABEL[r.severity]}</Badge>
                  </div>
                  <p className="text-xs text-slate-500 dark:text-slate-400">{r.description}</p>
                </div>
                <Badge tone={r.enabled ? 'green' : 'gray'}>{r.enabled ? '启用' : '停用'}</Badge>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function EdgeStatusCard() {
  const edge = useQuery({
    queryKey: ['edgeStatus'],
    queryFn: ({ signal }) => api.edgeStatus(signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });
  const d = edge.data;
  return (
    <Card>
      <CardHeader
        title="边缘采集（frps）"
        action={
          d ? (
            <Badge tone={!d.enabled ? 'gray' : d.connected ? 'green' : 'red'}>
              {!d.enabled ? '未启用' : d.connected ? '已连接' : '断开'}
            </Badge>
          ) : undefined
        }
      />
      <CardBody>
        {edge.isLoading || !d ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>
        ) : (
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-slate-500 dark:text-slate-400">最后事件</dt>
              <dd className="font-medium">{formatDateTime(d.last_event_ts)}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">24h 事件数</dt>
              <dd className="font-medium tabular-nums">{d.events_24h}</dd>
            </div>
            {d.error ? (
              <div className="col-span-2">
                <dt className="text-slate-500 dark:text-slate-400">错误</dt>
                <dd className="font-medium text-red-600 dark:text-red-400">{d.error}</dd>
              </div>
            ) : null}
          </dl>
        )}
      </CardBody>
    </Card>
  );
}

export function SecurityPage() {
  const qc = useQueryClient();
  const { lastAlert } = useConsole();
  const [status, setStatus] = useState<StatusFilter>('open');
  const [severity, setSeverity] = useState<'' | Alert['severity']>('');

  const alertsQuery = useQuery({
    queryKey: ['alerts', status],
    queryFn: ({ signal }) => api.alerts({ status, limit: 100 }, signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });

  // A live alert over the socket refreshes the list + counts.
  useEffect(() => {
    if (lastAlert) {
      void qc.invalidateQueries({ queryKey: ['alerts'] });
    }
  }, [lastAlert, qc]);

  const ack = useMutation({
    mutationFn: (id: string) => api.ackAlert(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['alerts'] }),
  });
  const ackAll = useMutation({
    mutationFn: () => api.ackAllAlerts(severity || undefined),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['alerts'] }),
  });

  const counts = alertsQuery.data?.open_counts ?? { critical: 0, warning: 0, info: 0 };
  const items = (alertsQuery.data?.items ?? []).filter(
    (a) => severity === '' || a.severity === severity,
  );

  return (
    <div className="space-y-4">
      <PageHeader title="安全" subtitle="告警、安全规则与 frps 边缘采集状态" />

      <div className="flex flex-wrap gap-3">
        {(['critical', 'warning', 'info'] as const).map((sev) => (
          <div
            key={sev}
            className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm dark:border-slate-800"
          >
            <Badge tone={severityTone(sev)}>{SEVERITY_LABEL[sev]}</Badge>
            <span className="tabular-nums font-semibold">{counts[sev]}</span>
            <span className="text-xs text-slate-400">未处理</span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-2">
          <Card>
            <CardHeader
              title="告警"
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => ackAll.mutate()}
                  disabled={ackAll.isPending || items.every((a) => a.status === 'acked')}
                >
                  全部已读{severity ? `（${SEVERITY_LABEL[severity]}）` : ''}
                </Button>
              }
            />
            <CardBody>
              <div className="mb-3 flex flex-wrap gap-2">
                <div className="flex gap-1" role="group" aria-label="按状态筛选">
                  {STATUS_FILTERS.map((f) => (
                    <Button
                      key={f.value}
                      size="sm"
                      variant={status === f.value ? 'primary' : 'ghost'}
                      onClick={() => setStatus(f.value)}
                    >
                      {f.label}
                    </Button>
                  ))}
                </div>
                <div className="flex gap-1" role="group" aria-label="按严重度筛选">
                  {SEVERITY_FILTERS.map((f) => (
                    <Button
                      key={f.value || 'all'}
                      size="sm"
                      variant={severity === f.value ? 'primary' : 'ghost'}
                      onClick={() => setSeverity(f.value)}
                    >
                      {f.label}
                    </Button>
                  ))}
                </div>
              </div>

              {alertsQuery.isLoading ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>
              ) : items.length === 0 ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">无告警</p>
              ) : (
                <ul className={scrollBox('lg', 'space-y-2')} tabIndex={0}>
                  {items.map((a) => (
                    <AlertCard key={a.id} alert={a} onAck={(id) => ack.mutate(id)} />
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>

        <div className="space-y-4">
          <RulesCard />
          <EdgeStatusCard />
        </div>
      </div>
    </div>
  );
}
