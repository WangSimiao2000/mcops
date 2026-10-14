import { useMemo, useReducer, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Application, BotStatus, GrantStatus, Interaction } from '@/api/types';
import { ApiError } from '@/api/http';
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { PageHeader } from '@/components/PageHeader';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { formatDateTime } from '@/lib/format';
import {
  chatReducer,
  highlightSegments,
  initialChatState,
  replyStatus,
  replyStatusTone,
} from '@/lib/bot';
import type { ReplyStatus } from '@/lib/bot';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { scrollBox } from '@/lib/scroll';

const SIM_QUICK = ['状态', '在线玩家', 'mod', '怎么加入', '帮助', '待审'];

function errText(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return String(err);
}

// --- status card -------------------------------------------------------------

function StatusCard({ status, isLoading }: { status: BotStatus | undefined; isLoading: boolean }) {
  return (
    <Card>
      <CardHeader
        title="机器人状态"
        action={
          status ? (
            <Badge tone={!status.enabled ? 'gray' : status.connected ? 'green' : 'red'}>
              {!status.enabled ? '未启用' : status.connected ? '已连接' : '断开'}
            </Badge>
          ) : undefined
        }
      />
      <CardBody>
        {isLoading || !status ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>
        ) : (
          <div className="space-y-3">
            {!status.enabled ? (
              <p className="rounded-md bg-slate-50 p-3 text-sm text-slate-600 dark:bg-slate-800/50 dark:text-slate-300">
                机器人未启用：旧机器人仍在运行，切换后这里会显示连接状态。
              </p>
            ) : null}
            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-slate-500 dark:text-slate-400">机器人名</dt>
                <dd className="font-medium">{status.robot_name ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">今日主动推送</dt>
                <dd className="font-medium tabular-nums">{status.pushes_today}</dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">维护窗口</dt>
                <dd className="font-medium">
                  {status.maintenance.active ? (
                    <Badge tone="yellow">
                      维护中{status.maintenance.until ? `（至 ${formatDateTime(status.maintenance.until)}）` : ''}
                    </Badge>
                  ) : (
                    <span className="text-slate-500 dark:text-slate-400">未维护</span>
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500 dark:text-slate-400">监控</dt>
                <dd className="font-medium">
                  {status.monitor.last_online === null ? (
                    <span className="text-slate-400">未知</span>
                  ) : status.monitor.last_online ? (
                    <Badge tone="green">在线</Badge>
                  ) : (
                    <Badge tone="red">离线（失败 {status.monitor.fail_count} 次）</Badge>
                  )}
                </dd>
              </div>
              {status.last_error ? (
                <div className="col-span-2 sm:col-span-3">
                  <dt className="text-slate-500 dark:text-slate-400">最近错误</dt>
                  <dd className="font-medium text-red-600 dark:text-red-400">{status.last_error}</dd>
                </div>
              ) : null}
            </dl>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function GroupsCard({ status }: { status: BotStatus | undefined }) {
  const groups = status?.groups ?? [];
  return (
    <Card>
      <CardHeader title="已加入群" />
      <CardBody>
        {groups.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">暂无群记录</p>
        ) : (
          <ul className={scrollBox('sm', 'divide-y divide-slate-100 dark:divide-slate-800')} tabIndex={0}>
            {groups.map((g) => (
              <li key={g.openid_masked} className="flex items-center justify-between gap-2 py-2 text-sm">
                <span className="font-mono">{g.openid_masked}</span>
                <span className="flex items-center gap-2">
                  <span className="text-xs text-slate-500 dark:text-slate-400">
                    {formatDateTime(g.added_at)}
                  </span>
                  {g.alert_group ? <Badge tone="blue">告警管理群</Badge> : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

// --- applications card -------------------------------------------------------

function ApplicationsCard() {
  const qc = useQueryClient();
  const [reject, setReject] = useState<Application | null>(null);
  const [lastReply, setLastReply] = useState<{ text: string; status: ReplyStatus } | null>(null);

  const apps = useQuery({
    queryKey: ['botApplications'],
    queryFn: ({ signal }) => api.botApplications(signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });

  const approve = useMutation({
    mutationFn: (name: string) => api.botApproveApplication(name),
    onSuccess: (r) => {
      setLastReply({ text: r.reply, status: replyStatus(r.reply) });
      void qc.invalidateQueries({ queryKey: ['botApplications'] });
      void qc.invalidateQueries({ queryKey: ['whitelist'] });
    },
    onError: (e) => setLastReply({ text: errText(e), status: 'failure' }),
  });
  const rejectMut = useMutation({
    mutationFn: (name: string) => api.botRejectApplication(name),
    onSuccess: (r) => {
      setLastReply({ text: r.reply, status: replyStatus(r.reply) });
      void qc.invalidateQueries({ queryKey: ['botApplications'] });
    },
    onError: (e) => setLastReply({ text: errText(e), status: 'failure' }),
  });

  const items = apps.data?.items ?? [];
  const busy = approve.isPending || rejectMut.isPending;

  return (
    <Card>
      <CardHeader title={`待审申请（${items.length}）`} />
      <CardBody className="space-y-3">
        {lastReply ? (
          <p
            role="status"
            className="flex items-center gap-2 rounded-md border p-2 text-sm"
          >
            <Badge tone={replyStatusTone(lastReply.status)}>
              {lastReply.status === 'success' ? '成功' : lastReply.status === 'partial' ? '部分' : lastReply.status === 'failure' ? '失败' : '结果'}
            </Badge>
            <span className="whitespace-pre-wrap">{lastReply.text}</span>
          </p>
        ) : null}

        <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="申请列表">
          <table className="w-full text-sm" data-responsive>
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="px-3 py-2 font-medium">游戏 ID</th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">申请人</th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">时间</th>
                <th className="px-3 py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {apps.isLoading ? (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-center text-slate-500 dark:text-slate-400">
                    加载中…
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-center text-slate-500 dark:text-slate-400">
                    暂无待审申请
                  </td>
                </tr>
              ) : (
                items.map((a) => (
                  <tr key={a.name} className="border-b border-slate-100 last:border-0 dark:border-slate-800">
                    <td className="px-3 py-2 font-medium">{a.name}</td>
                    <td className="hidden px-3 py-2 font-mono text-slate-500 sm:table-cell dark:text-slate-400">{a.openid_masked}</td>
                    <td className="hidden px-3 py-2 text-slate-500 sm:table-cell dark:text-slate-400">{formatDateTime(a.ts)}</td>
                    <td className="px-3 py-2">
                      <div className="flex justify-end gap-2">
                        <Button size="sm" disabled={busy} onClick={() => approve.mutate(a.name)}>
                          批准
                        </Button>
                        <Button size="sm" variant="danger" disabled={busy} onClick={() => setReject(a)}>
                          驳回
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </CardBody>

      <ConfirmDialog
        open={reject !== null}
        title={`驳回 ${reject?.name ?? ''} 的申请？`}
        description="驳回后该玩家不会被加入白名单。可稍后让其重新申请。"
        confirmLabel="驳回"
        onCancel={() => setReject(null)}
        onConfirm={() => {
          if (reject) rejectMut.mutate(reject.name);
          setReject(null);
        }}
      />
    </Card>
  );
}

// --- grants card -------------------------------------------------------------

const GRANT_LABEL: Record<GrantStatus, { text: string; tone: BadgeTone }> = {
  pending: { text: '待注册', tone: 'yellow' },
  activated: { text: '已注册', tone: 'green' },
  expired: { text: '逾期已移出', tone: 'red' },
  cancelled: { text: '已手动移出', tone: 'gray' },
};

function GrantsCard() {
  const grants = useQuery({
    queryKey: ['botGrants'],
    queryFn: ({ signal }) => api.botGrants(signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });
  const hours = grants.data?.activation_hours ?? 0;
  const items = grants.data?.items ?? [];
  const pending = items.filter((g) => g.status === 'pending').length;

  return (
    <Card>
      <CardHeader title={`获批待注册（${pending}）`} />
      <CardBody className="space-y-3">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {hours > 0
            ? `经申请获批后 ${hours} 小时内须进服并完成注册，否则自动移出白名单（人在服里时会等他下线）。期限在「设置 → 服务器配置 → QQ 机器人」里改。`
            : '未设置注册期限：获批后不会被自动移出白名单。'}
        </p>
        <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="获批记录">
          <table className="w-full text-sm" data-responsive>
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="px-3 py-2 font-medium">游戏 ID</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">获批时间</th>
                <th className="px-3 py-2 font-medium">截止 / 结清</th>
              </tr>
            </thead>
            <tbody>
              {grants.isLoading ? (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-center text-slate-500 dark:text-slate-400">
                    加载中…
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-center text-slate-500 dark:text-slate-400">
                    暂无获批记录
                  </td>
                </tr>
              ) : (
                items.map((g) => {
                  const label = GRANT_LABEL[g.status];
                  return (
                    <tr key={g.name} className="border-b border-slate-100 last:border-0 dark:border-slate-800">
                      <td className="px-3 py-2 font-medium">{g.name}</td>
                      <td className="px-3 py-2">
                        <Badge tone={label.tone}>{label.text}</Badge>
                      </td>
                      <td className="hidden px-3 py-2 text-slate-500 sm:table-cell dark:text-slate-400">
                        {formatDateTime(g.granted_at)}
                      </td>
                      <td className="px-3 py-2 text-slate-500 dark:text-slate-400">
                        {g.status === 'pending'
                          ? `${formatDateTime(g.deadline)} 前`
                          : g.resolved_at
                            ? formatDateTime(g.resolved_at)
                            : '-'}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

// --- simulator card ----------------------------------------------------------

function bubbleClasses(status: ReplyStatus | undefined): string {
  switch (status) {
    case 'success':
      return 'bg-green-100 text-green-900 dark:bg-green-900/40 dark:text-green-100';
    case 'partial':
      return 'bg-yellow-100 text-yellow-900 dark:bg-yellow-900/40 dark:text-yellow-100';
    case 'failure':
      return 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-100';
    default:
      return 'bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100';
  }
}

function SimulatorCard() {
  const [content, setContent] = useState('');
  const [asAdmin, setAsAdmin] = useState(false);
  const [chat, dispatch] = useReducer(chatReducer, initialChatState);

  const simulate = useMutation({
    mutationFn: (c: string) => api.botSimulate(c, asAdmin),
    onSuccess: (r) => dispatch({ type: 'reply', reply: r.reply }),
    onError: (e) => dispatch({ type: 'reply', reply: `❌ ${errText(e)}` }),
  });

  const send = (text: string) => {
    const t = text.trim();
    if (!t || simulate.isPending) return;
    dispatch({ type: 'send', input: t });
    setContent('');
    simulate.mutate(t);
  };

  return (
    <Card>
      <CardHeader
        title="指令模拟器"
        action={
          chat.turns.length > 0 ? (
            <button type="button" className="text-xs text-brand hover:underline" onClick={() => dispatch({ type: 'reset' })}>
              清空
            </button>
          ) : undefined
        }
      />
      <CardBody className="space-y-3">
        <p className="text-xs text-slate-500 dark:text-slate-400">模拟器不会把消息发到 QQ 群。</p>

        <div
          role="log"
          aria-live="polite"
          className="max-h-80 space-y-2 overflow-y-auto rounded-md border border-slate-200 p-3 dark:border-slate-800"
        >
          {chat.turns.length === 0 ? (
            <p className="text-sm text-slate-400">发送一条消息，查看机器人会怎么回复。</p>
          ) : (
            chat.turns.map((turn) => (
              <div key={turn.id} className={turn.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
                <div
                  className={
                    'max-w-[80%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ' +
                    (turn.role === 'user'
                      ? 'bg-brand text-brand-fg'
                      : bubbleClasses(turn.status))
                  }
                >
                  {turn.text}
                </div>
              </div>
            ))
          )}
        </div>

        <div className="flex flex-wrap gap-1">
          {SIM_QUICK.map((q) => (
            <Button key={q} size="sm" variant="ghost" disabled={simulate.isPending} onClick={() => send(q)}>
              {q}
            </Button>
          ))}
        </div>

        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            send(content);
          }}
        >
          <div className="flex-1">
            <Input
              label="模拟一条消息"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="例如：怎么加入"
              autoComplete="off"
            />
          </div>
          <label className="mb-2 flex items-center gap-1.5 text-sm text-slate-600 dark:text-slate-300">
            <input type="checkbox" checked={asAdmin} onChange={(e) => setAsAdmin(e.target.checked)} />
            以管理员身份
          </label>
          <Button type="submit" className="mb-1" disabled={simulate.isPending || content.trim() === ''}>
            发送
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

// --- interactions card -------------------------------------------------------

function OutputCell({ output, q }: { output: string; q: string }) {
  const [expanded, setExpanded] = useState(false);
  // 用行数裁剪（而非字符数）：长帮助文本折叠到 3 行，点击展开查看全文。
  const clampable = output.length > 60 || output.includes('\n');
  return (
    <div className="min-w-0">
      <span
        className={
          'block whitespace-pre-wrap break-words' +
          (clampable && !expanded ? ' line-clamp-3' : '')
        }
      >
        {highlightSegments(output, q).map((seg, i) =>
          seg.match ? (
            <mark key={i} className="rounded bg-yellow-200 px-0.5 dark:bg-yellow-500/40">
              {seg.text}
            </mark>
          ) : (
            <span key={i}>{seg.text}</span>
          ),
        )}
      </span>
      {clampable ? (
        <button
          type="button"
          className="mt-0.5 text-xs text-brand hover:underline"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? '收起' : '展开'}
        </button>
      ) : null}
    </div>
  );
}

function InteractionsCard() {
  const [q, setQ] = useState('');
  const interactions = useQuery({
    queryKey: ['botInteractions', q],
    queryFn: ({ signal }) => api.botInteractions({ limit: 100, q: q || undefined }, signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });
  const items: Interaction[] = interactions.data?.items ?? [];

  return (
    <Card>
      <CardHeader title="交互记录" />
      <CardBody className="space-y-3">
        <div className="max-w-sm">
          <Input
            label="搜索交互"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="匹配输入、输出或指令"
            autoComplete="off"
          />
        </div>
        <div className={scrollBox('lg', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="交互记录">
          <table className="w-full text-sm md:min-w-[56rem]" data-responsive>
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="px-3 py-2 font-medium">时间</th>
                <th className="hidden px-3 py-2 font-medium lg:table-cell">群</th>
                <th className="hidden px-3 py-2 font-medium lg:table-cell">发言人</th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">管理员</th>
                <th className="px-3 py-2 font-medium">指令</th>
                <th className="hidden px-3 py-2 font-medium md:table-cell">输入</th>
                <th className="px-3 py-2 font-medium">输出</th>
              </tr>
            </thead>
            <tbody>
              {interactions.isLoading ? (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-slate-500 dark:text-slate-400">
                    加载中…
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-slate-500 dark:text-slate-400">
                    无匹配记录
                  </td>
                </tr>
              ) : (
                items.map((it) => (
                  <tr key={it.id} className="border-b border-slate-100 align-top last:border-0 dark:border-slate-800">
                    <td className="px-3 py-2 text-slate-500 dark:text-slate-400">{formatDateTime(it.ts)}</td>
                    <td className="hidden px-3 py-2 font-mono text-xs lg:table-cell">{it.group_masked}</td>
                    <td className="hidden px-3 py-2 font-mono text-xs lg:table-cell">{it.openid_masked}</td>
                    <td className="hidden px-3 py-2 sm:table-cell">{it.admin ? <Badge tone="blue">管理员</Badge> : null}</td>
                    <td className="px-3 py-2 font-mono text-xs">{it.cmd}</td>
                    <td className="hidden px-3 py-2 md:table-cell">
                      <span className="whitespace-pre-wrap break-words">
                        {highlightSegments(it.input, q).map((seg, i) =>
                          seg.match ? (
                            <mark key={i} className="rounded bg-yellow-200 px-0.5 dark:bg-yellow-500/40">
                              {seg.text}
                            </mark>
                          ) : (
                            <span key={i}>{seg.text}</span>
                          ),
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <OutputCell output={it.output} q={q} />
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

export function BotPage() {
  const statusQuery = useQuery({
    queryKey: ['botStatus'],
    queryFn: ({ signal }) => api.botStatus(signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });

  const status = useMemo(() => statusQuery.data, [statusQuery.data]);

  return (
    <div className="space-y-4">
      <PageHeader title="QQ 机器人" subtitle="连接状态、白名单申请审批、指令模拟与交互记录" />

      {statusQuery.isError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法获取机器人状态，正在重试…
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <StatusCard status={status} isLoading={statusQuery.isLoading} />
        </div>
        <GroupsCard status={status} />
      </div>

      <ApplicationsCard />
      <GrantsCard />
      <SimulatorCard />
      <InteractionsCard />
    </div>
  );
}
