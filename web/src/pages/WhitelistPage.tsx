import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { WhitelistEntry } from '@/api/types';
import { ApiError } from '@/api/http';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState } from '@/components/EmptyState';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { gameIdError, replyStatus, replyStatusTone } from '@/lib/bot';
import type { ReplyStatus } from '@/lib/bot';
import { modOn, useCapabilities } from '@/lib/capabilities';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { scrollBox } from '@/lib/scroll';

function errText(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return String(err);
}

function UuidCell({ uuid }: { uuid: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(uuid);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // clipboard 不可用时静默，用户仍可手动选中。
    }
  };
  return (
    <button
      type="button"
      onClick={() => void copy()}
      title="点击复制 UUID"
      className="font-mono text-xs text-slate-600 hover:underline dark:text-slate-300"
    >
      {uuid}
      <span className="ml-1 text-slate-400">{copied ? '已复制' : '⧉'}</span>
    </button>
  );
}

function RegisteredBadge({ value }: { value: boolean | null }) {
  if (value === null) return <Badge tone="gray">未知</Badge>;
  if (value) return <Badge tone="green">已注册</Badge>;
  return (
    <span title="白名单内但未注册，任何人可抢注冒用">
      <Badge tone="red">未注册</Badge>
    </span>
  );
}

export function WhitelistPage() {
  const qc = useQueryClient();
  const { data: caps } = useCapabilities();
  const easyauth = modOn(caps, 'easyauth');
  const [name, setName] = useState('');
  const [touched, setTouched] = useState(false);
  const [remove, setRemove] = useState<WhitelistEntry | null>(null);
  const [lastReply, setLastReply] = useState<{ text: string; status: ReplyStatus } | null>(null);

  const list = useQuery({
    queryKey: ['whitelist'],
    queryFn: ({ signal }) => api.whitelist(signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });

  const add = useMutation({
    mutationFn: (n: string) => api.whitelistAdd(n),
    onSuccess: (r) => {
      setLastReply({ text: r.reply, status: replyStatus(r.reply) });
      setName('');
      setTouched(false);
      void qc.invalidateQueries({ queryKey: ['whitelist'] });
    },
    onError: (e) => setLastReply({ text: errText(e), status: 'failure' }),
  });

  const removeMut = useMutation({
    mutationFn: (n: string) => api.whitelistRemove(n),
    onSuccess: (r) => {
      setLastReply({ text: r.reply, status: replyStatus(r.reply) });
      void qc.invalidateQueries({ queryKey: ['whitelist'] });
    },
    onError: (e) => setLastReply({ text: errText(e), status: 'failure' }),
  });

  const validationError = touched ? gameIdError(name) : null;
  const items = list.data?.items ?? [];

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (gameIdError(name) !== null) return;
    add.mutate(name);
  };

  return (
    <div className="space-y-4">
      <PageHeader title="白名单" subtitle="离线模式按原始大小写计算 UUID 写入，再重载回读确认" />

      <Card>
        <CardBody>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            离线模式下，系统按<strong>原始大小写</strong>计算 UUID 直接写入白名单文件，再让服务端重载并回读确认。
            请勿使用游戏内 <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">/whitelist add</code>，
            它会写入错误的 UUID。
          </p>
        </CardBody>
      </Card>

      {lastReply ? (
        <p role="status" className="flex items-center gap-2 rounded-md border border-slate-200 p-2 text-sm dark:border-slate-800">
          <Badge tone={replyStatusTone(lastReply.status)}>
            {lastReply.status === 'success' ? '成功' : lastReply.status === 'partial' ? '部分' : lastReply.status === 'failure' ? '失败' : '结果'}
          </Badge>
          <span className="whitespace-pre-wrap">{lastReply.text}</span>
        </p>
      ) : null}

      <Card>
        <CardHeader title="添加白名单" />
        <CardBody>
          <form className="flex items-end gap-2" onSubmit={submit}>
            <div className="max-w-xs flex-1">
              <Input
                label="游戏 ID"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => setTouched(true)}
                placeholder="例如 Steve_01"
                autoComplete="off"
                hint="区分大小写，须与进服时一致"
                error={validationError ?? undefined}
              />
            </div>
            <Button type="submit" className="mb-1" disabled={add.isPending}>
              添加
            </Button>
          </form>
        </CardBody>
      </Card>

      {list.isError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法获取白名单，正在重试…
        </p>
      ) : null}

      <Card>
        <CardHeader title={`白名单（${items.length}）`} />
        <CardBody className="p-0">
          <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="白名单">
            <table className="w-full text-sm" data-responsive>
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="px-4 py-2 font-medium">游戏 ID</th>
                  <th className="hidden px-4 py-2 font-medium lg:table-cell">UUID</th>
                  {easyauth ? <th className="px-4 py-2 font-medium">EasyAuth</th> : null}
                  <th className="hidden px-2 py-2 text-right font-medium sm:table-cell">绑定 QQ 数</th>
                  <th className="px-4 py-2 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {list.isLoading ? (
                  <tr>
                    <td colSpan={easyauth ? 5 : 4} className="px-4 py-6 text-center text-slate-500 dark:text-slate-400">
                      加载中…
                    </td>
                  </tr>
                ) : items.length === 0 ? (
                  <tr>
                    <td colSpan={easyauth ? 5 : 4} className="px-4 py-10 text-center">
                      <EmptyState hint="白名单为空，在上方「添加白名单」录入第一位玩家。" />
                    </td>
                  </tr>
                ) : (
                  items.map((w) => (
                    <tr
                      key={w.name}
                      className={
                        'border-b border-slate-100 last:border-0 dark:border-slate-800' +
                        (easyauth && w.easyauth_registered === false ? ' bg-red-50/50 dark:bg-red-900/10' : '')
                      }
                    >
                      <td className="px-4 py-2 font-medium">{w.name}</td>
                      <td className="hidden px-4 py-2 lg:table-cell">
                        <UuidCell uuid={w.uuid} />
                      </td>
                      {easyauth ? (
                        <td className="px-4 py-2">
                          <RegisteredBadge value={w.easyauth_registered} />
                        </td>
                      ) : null}
                      <td className="hidden px-2 py-2 text-right tabular-nums sm:table-cell">{w.members.length}</td>
                      <td className="px-4 py-2 text-right">
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={removeMut.isPending}
                          onClick={() => setRemove(w)}
                        >
                          移除
                        </Button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>

      <ConfirmDialog
        open={remove !== null}
        title={`移除 ${remove?.name ?? ''}？`}
        description="移除后该玩家无法进服。如需恢复，需重新添加白名单。"
        confirmLabel="移除"
        onCancel={() => setRemove(null)}
        onConfirm={() => {
          if (remove) removeMut.mutate(remove.name);
          setRemove(null);
        }}
      />
    </div>
  );
}
