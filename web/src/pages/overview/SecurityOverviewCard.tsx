import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatDateTime } from '@/lib/format';
import { SEVERITY_LABEL, severityTone, totalOpen } from '@/lib/security';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { scrollBox } from '@/lib/scroll';

export function SecurityOverviewCard() {
  const alertsQuery = useQuery({
    queryKey: ['alerts', 'open'],
    queryFn: ({ signal }) => api.alerts({ status: 'open', limit: 3 }, signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });

  const counts = alertsQuery.data?.open_counts ?? { critical: 0, warning: 0, info: 0 };
  const recent = alertsQuery.data?.items ?? [];

  return (
    <Card>
      <CardHeader
        title="安全概况"
        action={
          <Link to="/security" className="text-xs text-brand hover:underline">
            查看全部
          </Link>
        }
      />
      <CardBody>
        <div className="mb-3 flex flex-wrap gap-2">
          {(['critical', 'warning', 'info'] as const).map((sev) => (
            <span key={sev} className="flex items-center gap-1 text-sm">
              <Badge tone={severityTone(sev)}>{SEVERITY_LABEL[sev]}</Badge>
              <span className="tabular-nums font-semibold">{counts[sev]}</span>
            </span>
          ))}
          <span className="ml-auto text-xs text-slate-400">
            共 {totalOpen(counts)} 条未处理
          </span>
        </div>

        {alertsQuery.isLoading ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>
        ) : recent.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">暂无未处理告警</p>
        ) : (
          <ul className={scrollBox('sm', 'divide-y divide-slate-100 dark:divide-slate-800')} tabIndex={0}>
            {recent.map((a) => (
              <li key={a.id} className="flex items-start gap-2 py-2 text-sm">
                <Badge tone={severityTone(a.severity)}>{SEVERITY_LABEL[a.severity]}</Badge>
                <div className="min-w-0">
                  <p className="truncate font-medium">{a.title}</p>
                  <p className="text-xs text-slate-400">{formatDateTime(a.last_ts)}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
