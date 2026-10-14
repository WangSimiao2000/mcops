import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { api } from '@/api';
import type { PlayerIp, Session } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { IpText } from '@/components/IpText';
import { RecentEventsCard } from '@/pages/overview/RecentEventsCard';
import { formatDateTime, formatDurationBetween } from '@/lib/format';
import { flagMeta } from '@/lib/security';
import { scrollBox } from '@/lib/scroll';

function YesNo({ value, yes = '是', no = '否' }: { value: boolean | null; yes?: string; no?: string }) {
  if (value === null) return <span className="text-slate-400">—</span>;
  return <span>{value ? yes : no}</span>;
}

function SessionRow({ s }: { s: Session }) {
  return (
    <tr className="border-b border-slate-100 last:border-0 dark:border-slate-800">
      <td className="px-3 py-2 text-slate-500 dark:text-slate-400">{formatDateTime(s.login_at)}</td>
      <td className="px-3 py-2 tabular-nums">{formatDurationBetween(s.login_at, s.logout_at)}</td>
      <td className="px-3 py-2">
        <IpText ip={s.ip} trusted={s.ip_trusted} link />
      </td>
      <td className="px-3 py-2">
        <YesNo value={s.authenticated} />
      </td>
      <td className="px-3 py-2">
        <YesNo value={s.carpet_client} />
      </td>
      <td className="px-3 py-2 text-slate-500 dark:text-slate-400">{s.leave_reason ?? '—'}</td>
    </tr>
  );
}

function IpRow({ ip }: { ip: PlayerIp }) {
  return (
    <li className="flex items-center justify-between py-2 text-sm">
      <IpText ip={ip.ip} trusted={ip.trusted} link />
      <span className="text-xs text-slate-500 dark:text-slate-400">
        {ip.sessions} 次 · {formatDateTime(ip.first_seen)} → {formatDateTime(ip.last_seen)}
      </span>
    </li>
  );
}

export function PlayerDetailPage() {
  const { name = '' } = useParams();
  const detail = useQuery({
    queryKey: ['player', name],
    queryFn: ({ signal }) => api.player(name, signal),
    enabled: name !== '',
  });

  if (detail.isLoading) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>;
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="space-y-3">
        <Link to="/players" className="text-sm text-brand hover:underline">
          ← 返回玩家列表
        </Link>
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法加载玩家 {name} 的信息。
        </p>
      </div>
    );
  }

  const { player, sessions, ips, events } = detail.data;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/players" className="text-sm text-brand hover:underline">
          ← 返回
        </Link>
        <h1 className="text-lg font-semibold">{player.name}</h1>
        {player.online ? <Badge tone="green">在线</Badge> : <Badge tone="gray">离线</Badge>}
        {player.is_op ? <Badge tone="blue">OP</Badge> : null}
        {player.flags.map((f) => {
          const m = flagMeta(f);
          return (
            <span key={f} title={m.title}>
              <Badge tone={m.tone}>{m.label}</Badge>
            </span>
          );
        })}
      </div>

      <Card>
        <CardHeader title="基本信息" />
        <CardBody>
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-slate-500 dark:text-slate-400">首次见到</dt>
              <dd className="font-medium">{formatDateTime(player.first_seen)}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">最后见到</dt>
              <dd className="font-medium">{formatDateTime(player.last_seen)}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">会话数</dt>
              <dd className="font-medium tabular-nums">{player.sessions}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">最后 IP</dt>
              <dd>
                <IpText ip={player.last_ip} trusted={player.last_ip_trusted} link />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">白名单</dt>
              <dd>
                <YesNo value={player.whitelisted} />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">EasyAuth 注册</dt>
              <dd>
                <YesNo value={player.easyauth_registered} yes="已注册" no="未注册" />
              </dd>
            </div>
          </dl>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="会话时间线" />
        <CardBody className="p-0">
          <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="会话记录">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="px-3 py-2 font-medium">登录时间</th>
                  <th className="px-3 py-2 font-medium">时长</th>
                  <th className="px-3 py-2 font-medium">IP</th>
                  <th className="px-3 py-2 font-medium">输密码</th>
                  <th className="px-3 py-2 font-medium">carpet</th>
                  <th className="px-3 py-2 font-medium">离开原因</th>
                </tr>
              </thead>
              <tbody>
                {sessions.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-center text-slate-500 dark:text-slate-400">
                      暂无会话记录
                    </td>
                  </tr>
                ) : (
                  sessions.map((s) => <SessionRow key={s.id} s={s} />)
                )}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="关联 IP" />
          <CardBody>
            {ips.length === 0 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">暂无 IP 记录</p>
            ) : (
              <ul className={scrollBox('md', 'divide-y divide-slate-100 dark:divide-slate-800')} tabIndex={0}>
                {ips.map((ip) => (
                  <IpRow key={ip.ip} ip={ip} />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
        <RecentEventsCard events={events} isLoading={false} />
      </div>
    </div>
  );
}
