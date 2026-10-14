import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '@/api';
import type { ServerStatus } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { IpText } from '@/components/IpText';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { playersSummary, splitPlayers } from '@/lib/players';
import { scrollBox } from '@/lib/scroll';

export function PlayersCard({ status }: { status: ServerStatus | null }) {
  const players = status?.players ?? null;
  const split = players ? splitPlayers(players) : null;

  // Enrich online names with their last-known IP from the players directory.
  const dir = useQuery({
    queryKey: ['players', ''],
    queryFn: ({ signal }) => api.players({ limit: 200 }, signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });
  const byName = new Map((dir.data?.items ?? []).map((p) => [p.name, p]));
  const empty = !split || (split.real.length === 0 && split.fake.length === 0);

  return (
    <Card>
      <CardHeader title={`在线玩家 ${players ? `(${playersSummary(players)})` : ''}`} />
      <CardBody>
        {empty ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">当前无玩家在线</p>
        ) : (
          <div className="space-y-3">
            {split.real.length > 0 ? (
              <ul className={scrollBox('sm', 'space-y-1.5')} tabIndex={0} aria-label="真人玩家">
                {split.real.map((name) => {
                  const p = byName.get(name);
                  return (
                    <li key={name} className="flex items-center justify-between gap-2 text-sm">
                      <Link
                        to={`/players/${encodeURIComponent(name)}`}
                        className="font-medium text-brand hover:underline"
                      >
                        {name}
                      </Link>
                      {p ? (
                        <IpText ip={p.last_ip} trusted={p.last_ip_trusted} link />
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : null}
            {split.fake.length > 0 ? (
              <div>
                <p className="mb-1.5 text-xs text-slate-500 dark:text-slate-400">
                  假人（carpet 召唤，服务器列表里显示为 Anonymous Player）
                </p>
                <ul className={scrollBox('sm', 'space-y-1.5')} tabIndex={0} aria-label="假人">
                  {split.fake.map((name) => (
                    <li key={name} className="flex items-center justify-between gap-2 text-sm">
                      <span className="font-medium text-slate-700 dark:text-slate-300">
                        {name}
                      </span>
                      <Badge>假人</Badge>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
