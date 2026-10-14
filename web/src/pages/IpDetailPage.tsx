import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { api } from '@/api';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatDateTime } from '@/lib/format';
import { scrollBox } from '@/lib/scroll';

export function IpDetailPage() {
  const { ip = '' } = useParams();
  const info = useQuery({
    queryKey: ['ip', ip],
    queryFn: ({ signal }) => api.ip(ip, signal),
    enabled: ip !== '',
  });

  if (info.isLoading) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>;
  }
  if (info.isError || !info.data) {
    return (
      <div className="space-y-3">
        <Link to="/players" className="text-sm text-brand hover:underline">
          ← 返回玩家列表
        </Link>
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法加载 IP {ip} 的信息。
        </p>
      </div>
    );
  }

  const d = info.data;
  const multiAccount = d.accounts.length > 1;
  const proxyEntries = Object.entries(d.frps.by_proxy);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/players" className="text-sm text-brand hover:underline">
          ← 返回
        </Link>
        <h1 className="font-mono text-lg font-semibold">{d.ip}</h1>
        {d.banned ? <Badge tone="red">已封禁</Badge> : <Badge tone="gray">未封禁</Badge>}
      </div>

      {multiAccount ? (
        <p
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
        >
          ⚠ 一个 IP 多个账号：此 IP 关联了 {d.accounts.length} 个账号，可能是共享网络或冒用。
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="概览" />
          <CardBody>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-slate-500 dark:text-slate-400">首次出现</dt>
                <dd className="font-medium">{formatDateTime(d.first_seen)}</dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">末次出现</dt>
                <dd className="font-medium">{formatDateTime(d.last_seen)}</dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">会话数</dt>
                <dd className="font-medium tabular-nums">{d.sessions}</dd>
              </div>
            </dl>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title={`关联账号 (${d.accounts.length})`} />
          <CardBody>
            {d.accounts.length === 0 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">无关联账号</p>
            ) : (
              <ul className={scrollBox('md', 'divide-y divide-slate-100 dark:divide-slate-800')} tabIndex={0}>
                {d.accounts.map((a) => (
                  <li key={a.name} className="flex items-center justify-between py-2 text-sm">
                    <Link to={`/players/${encodeURIComponent(a.name)}`} className="font-medium text-brand hover:underline">
                      {a.name}
                    </Link>
                    <span className="text-xs text-slate-500 dark:text-slate-400">{a.sessions} 次会话</span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="frps 连接统计" />
        <CardBody>
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-slate-500 dark:text-slate-400">总连接</dt>
              <dd className="font-medium tabular-nums">{d.frps.connections}</dd>
            </div>
            {proxyEntries.map(([proxy, n]) => (
              <div key={proxy}>
                <dt className="text-slate-500 dark:text-slate-400">{proxy}</dt>
                <dd className="font-medium tabular-nums">{n}</dd>
              </div>
            ))}
            <div>
              <dt className="text-slate-500 dark:text-slate-400">首次 / 末次</dt>
              <dd className="font-medium">
                {formatDateTime(d.frps.first_seen)} → {formatDateTime(d.frps.last_seen)}
              </dd>
            </div>
          </dl>
          {proxyEntries.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">无 frps 代理分项数据</p>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}
