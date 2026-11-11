import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import type { PlayerIp, ResetPasswordResponse, Session } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
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

/** 重置 EasyAuth 密码：确认后生成随机临时密码，只显示这一次。 */
function ResetPassword({ name, registered }: { name: string; registered: boolean | null }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<ResetPasswordResponse | null>(null);
  const [copied, setCopied] = useState(false);
  const mut = useMutation({
    mutationFn: () => api.resetPassword(name),
    onSuccess: (r) => {
      setResult(r);
      setCopied(false);
      void qc.invalidateQueries({ queryKey: ['player', name] });
    },
  });
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  const action = registered === false ? '设置初始密码' : '重置密码';

  return (
    <div className="mt-4 border-t border-slate-100 pt-4 text-sm dark:border-slate-800">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" size="sm" onClick={() => setConfirming(true)} disabled={mut.isPending}>
          {mut.isPending ? '处理中…' : action}
        </Button>
        <span className="text-slate-500 dark:text-slate-400">
          {registered === false
            ? '这个账号还没注册，先替玩家设好密码，别人就没法抢注。'
            : '玩家忘记密码时用：生成一个临时密码，玩家进服后自己改掉。'}
        </span>
      </div>
      {mut.isError ? (
        <p role="alert" className="mt-2 text-red-600 dark:text-red-400">
          {mut.error instanceof ApiError ? mut.error.detail : '操作失败'}
        </p>
      ) : null}
      {result ? (
        <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100">
          <p>
            {result.mode === 'register' ? '已替 ' : '已把 '}
            <b>{result.name}</b>
            {result.mode === 'register' ? ' 注册，初始密码：' : ' 的密码改成：'}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="rounded bg-white px-2 py-1 font-mono text-base tracking-wider dark:bg-slate-900">
              {result.password}
            </code>
            <Button size="sm" variant="secondary" onClick={() => void copy(result.password)}>
              {copied ? '已复制' : '复制'}
            </Button>
          </div>
          <p className="mt-2 text-xs">
            这个密码只显示这一次，离开页面就看不到了。请私聊发给玩家，不要发到群里。玩家进服后先 /login 这个密码，再用
            <code className="mx-1">/account changePassword 旧密码 新密码</code>改成自己的。
          </p>
        </div>
      ) : null}
      <ConfirmDialog
        open={confirming}
        title={`${action}：${name}`}
        description={
          <>
            先确认找你的人就是 {name} 本人（比如用对方平时的 QQ 号联系确认），再继续。离线模式下名字不是秘密，替冒名者改密码等于把账号交给冒名者。
            {registered === false ? null : ' 原来的密码会立即失效。'}
          </>
        }
        confirmLabel={action}
        onConfirm={() => {
          setConfirming(false);
          mut.mutate();
        }}
        onCancel={() => setConfirming(false)}
      />
    </div>
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
          <ResetPassword name={player.name} registered={player.easyauth_registered} />
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
