import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api } from '@/api';
import type { Player } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { IpText } from '@/components/IpText';
import { PageHeader } from '@/components/PageHeader';
import { formatDateTime } from '@/lib/format';
import { flagMeta, isHighRisk, sortPlayers } from '@/lib/security';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { scrollBox } from '@/lib/scroll';

function OnlineDot({ online }: { online: boolean }) {
  return (
    <span
      aria-label={online ? '在线' : '离线'}
      title={online ? '在线' : '离线'}
      className={
        online
          ? 'inline-block h-2.5 w-2.5 rounded-full bg-green-500'
          : 'inline-block h-2.5 w-2.5 rounded-full bg-slate-300 dark:bg-slate-600'
      }
    />
  );
}

function FlagBadges({ player }: { player: Player }) {
  if (player.flags.length === 0 && !player.is_op) {
    return <span className="text-slate-300 dark:text-slate-600">—</span>;
  }
  return (
    <span className="flex flex-wrap gap-1">
      {player.flags.map((f) => {
        const m = flagMeta(f);
        return (
          <span key={f} title={m.title}>
            <Badge tone={m.tone}>{m.label}</Badge>
          </span>
        );
      })}
    </span>
  );
}

export function PlayersPage() {
  const [q, setQ] = useState('');
  const navigate = useNavigate();

  const playersQuery = useQuery({
    queryKey: ['players', q],
    queryFn: ({ signal }) => api.players({ q: q || undefined, limit: 200 }, signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });

  const rows = useMemo(() => sortPlayers(playersQuery.data?.items ?? []), [playersQuery.data]);

  return (
    <div className="space-y-4">
      <PageHeader title="玩家" subtitle="历史与在线玩家、来源 IP 与风险标记" />

      <div className="max-w-sm">
        <Input
          label="搜索玩家"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="按名字过滤"
          autoComplete="off"
        />
      </div>

      {playersQuery.isError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法获取玩家列表，正在重试…
        </p>
      ) : null}

      <Card>
        <CardBody className="p-0">
          <div className={scrollBox('lg', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="玩家列表">
            <table className="w-full text-sm" data-responsive>
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="px-4 py-2 font-medium">名字</th>
                  <th className="px-2 py-2 font-medium">状态</th>
                  <th className="hidden px-4 py-2 font-medium md:table-cell">最后上线</th>
                  <th className="hidden px-4 py-2 font-medium lg:table-cell">最后 IP</th>
                  <th className="hidden px-2 py-2 text-right font-medium sm:table-cell">IP 数</th>
                  <th className="hidden px-2 py-2 text-right font-medium sm:table-cell">会话数</th>
                  <th className="px-4 py-2 font-medium">标记</th>
                  <th className="px-2 py-2 font-medium">OP</th>
                </tr>
              </thead>
              <tbody>
                {playersQuery.isLoading ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-6 text-center text-slate-500 dark:text-slate-400">
                      加载中…
                    </td>
                  </tr>
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-6 text-center text-slate-500 dark:text-slate-400">
                      无匹配玩家
                    </td>
                  </tr>
                ) : (
                  rows.map((p) => (
                    <tr
                      key={p.name}
                      tabIndex={0}
                      role="link"
                      aria-label={`查看 ${p.name} 详情`}
                      onClick={() => navigate(`/players/${encodeURIComponent(p.name)}`)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          navigate(`/players/${encodeURIComponent(p.name)}`);
                        }
                      }}
                      className={
                        'cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 focus:bg-slate-50 focus:outline-none dark:border-slate-800 dark:hover:bg-slate-800/50 dark:focus:bg-slate-800/50' +
                        (isHighRisk(p) ? ' bg-red-50/50 dark:bg-red-900/10' : '')
                      }
                    >
                      <td className="px-4 py-2 font-medium">{p.name}</td>
                      <td className="px-2 py-2">
                        <OnlineDot online={p.online} />
                      </td>
                      <td className="hidden px-4 py-2 text-slate-500 md:table-cell dark:text-slate-400">
                        {formatDateTime(p.last_seen)}
                      </td>
                      <td className="hidden px-4 py-2 lg:table-cell">
                        <IpText ip={p.last_ip} trusted={p.last_ip_trusted} />
                      </td>
                      <td className="hidden px-2 py-2 text-right tabular-nums sm:table-cell">{p.distinct_ips}</td>
                      <td className="hidden px-2 py-2 text-right tabular-nums sm:table-cell">{p.sessions}</td>
                      <td className="px-4 py-2">
                        <FlagBadges player={p} />
                      </td>
                      <td className="px-2 py-2">{p.is_op ? <Badge tone="blue">OP</Badge> : null}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
