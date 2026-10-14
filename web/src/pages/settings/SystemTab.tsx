import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';

import { ApiError } from '@/api/http';
import { systemApi, type CheckStatus, type DiagCheck } from '@/api/system';
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { formatDateTime } from '@/lib/format';

const TONE: Record<CheckStatus, { tone: BadgeTone; label: string }> = {
  ok: { tone: 'green', label: '正常' },
  warn: { tone: 'yellow', label: '注意' },
  fail: { tone: 'red', label: '异常' },
  info: { tone: 'gray', label: '信息' },
};

function err(e: unknown): string {
  return e instanceof ApiError ? e.detail : e instanceof Error ? e.message : '操作失败';
}

function groupBy(items: DiagCheck[]): [string, DiagCheck[]][] {
  const m = new Map<string, DiagCheck[]>();
  for (const it of items) m.set(it.group, [...(m.get(it.group) ?? []), it]);
  return [...m.entries()];
}

export function SystemTab() {
  return (
    <div className="space-y-4">
      <DiagnosticsCard />
      <UpdatesCard />
    </div>
  );
}

function DiagnosticsCard() {
  const q = useQuery({ queryKey: ['diagnostics'], queryFn: () => systemApi.diagnostics() });
  const items = q.data?.items ?? [];
  const bad = items.filter((i) => i.status === 'fail').length;
  const warn = items.filter((i) => i.status === 'warn').length;
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <h2 className="text-[0.9375rem] font-semibold text-slate-800 dark:text-slate-100">自检</h2>
        <div className="flex items-center gap-2 text-xs">
          {q.data ? (
            <span className="text-slate-500 dark:text-slate-400">
              {bad ? `${bad} 项异常 · ` : ''}
              {warn ? `${warn} 项需注意 · ` : ''}共 {items.length} 项
            </span>
          ) : null}
          <Button size="sm" variant="secondary" disabled={q.isFetching} onClick={() => void q.refetch()}>
            {q.isFetching ? '检查中…' : '重新检查'}
          </Button>
        </div>
      </div>
      <CardBody className="space-y-4">
        {q.isError ? <p className="text-sm text-red-600 dark:text-red-400">{err(q.error)}</p> : null}
        {groupBy(items).map(([group, list]) => (
          <section key={group}>
            <h3 className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">{group}</h3>
            <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
              {list.map((c) => (
                <li key={c.id} className="flex flex-wrap items-start justify-between gap-2 px-3 py-2 text-sm">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">{c.title}</div>
                    <div className="break-words text-slate-600 dark:text-slate-300">{c.detail}</div>
                    {c.fix && c.status !== 'ok' ? (
                      <div className="mt-0.5 break-words text-xs text-slate-500 dark:text-slate-400">
                        建议：{c.fix}
                      </div>
                    ) : null}
                  </div>
                  <Badge tone={TONE[c.status].tone}>{TONE[c.status].label}</Badge>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </CardBody>
    </Card>
  );
}

function UpdatesCard() {
  const qc = useQueryClient();
  const ref = useRef<HTMLInputElement>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [phase, setPhase] = useState<'idle' | 'restarting' | 'done' | 'timeout'>('idle');
  const q = useQuery({ queryKey: ['updates'], queryFn: () => systemApi.updates() });

  const upload = useMutation({
    mutationFn: (f: File) => systemApi.uploadBundle(f),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['updates'] }),
  });
  const install = useMutation({
    mutationFn: (rev: string) => systemApi.installRevision(rev),
    onSuccess: async (_r, rev) => {
      setPhase('restarting');
      // agent 会在 2 秒后安装并重启：等它先下线再回来，最多 3 分钟
      const deadline = Date.now() + 180_000;
      await new Promise((r) => setTimeout(r, 6000));
      while (Date.now() < deadline) {
        try {
          const u = await systemApi.updates();
          if (u.current === rev) {
            setPhase('done');
            await qc.invalidateQueries();
            return;
          }
        } catch {
          /* 重启中，继续等 */
        }
        await new Promise((r) => setTimeout(r, 3000));
      }
      setPhase('timeout');
    },
  });

  return (
    <Card>
      <CardHeader title="升级 mc-ops" />
      <CardBody className="space-y-3 text-sm">
        <p className="text-slate-600 dark:text-slate-300">
          当前版本 <b className="font-mono">{q.data?.current ?? '未知'}</b>。在开发机运行{' '}
          <code className="font-mono text-xs">bash deploy/make-bundle.sh out --tar</code> 生成{' '}
          <code className="font-mono text-xs">out.tar.gz</code>
          ，在这里上传即可升级；升级只重启 mc-ops，不影响正在运行的 Minecraft。下面保留的旧版本可以一键回滚。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={upload.isPending} onClick={() => ref.current?.click()}>
            {upload.isPending ? '上传中…' : '上传安装包（.tar.gz）'}
          </Button>
          <input
            ref={ref}
            type="file"
            accept=".tar.gz,.tgz,application/gzip"
            className="hidden"
            aria-label="上传 mc-ops 安装包"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload.mutate(f);
              e.target.value = '';
            }}
          />
          {upload.data ? (
            <span className="text-green-700 dark:text-green-400">已上传版本 {upload.data.revision}</span>
          ) : null}
          {upload.isError ? (
            <span className="text-red-600 dark:text-red-400">{err(upload.error)}</span>
          ) : null}
        </div>
        <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
          {(q.data?.bundles ?? []).map((b) => (
            <li key={b.revision} className="flex items-center justify-between gap-2 px-3 py-2">
              <div>
                <span className="font-mono">{b.revision}</span>
                <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">
                  {formatDateTime(b.installed_at)}
                </span>
              </div>
              {b.current ? (
                <Badge tone="green">当前</Badge>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={install.isPending || phase === 'restarting'}
                  onClick={() => setTarget(b.revision)}
                >
                  安装此版本
                </Button>
              )}
            </li>
          ))}
          {q.data && q.data.bundles.length === 0 ? (
            <li className="px-3 py-2 text-slate-500">还没有缓存的安装包</li>
          ) : null}
        </ul>
        {phase === 'restarting' ? <p>正在安装并重启 mc-ops…</p> : null}
        {phase === 'done' ? (
          <p className="text-green-700 dark:text-green-400">
            升级完成，页面数据已刷新。建议强制刷新一次浏览器加载新前端。
          </p>
        ) : null}
        {phase === 'timeout' ? (
          <p className="text-red-600 dark:text-red-400">
            3 分钟内没有切换到新版本，请到服务器查看 ~/.cache/mcops-bundles/&lt;版本&gt;/install.log
          </p>
        ) : null}
        {install.isError ? <p className="text-red-600 dark:text-red-400">{err(install.error)}</p> : null}
      </CardBody>
      <ConfirmDialog
        open={target !== null}
        title={`安装 mc-ops ${target ?? ''}？`}
        description="mc-ops 会安装这个版本并重启自己（约半分钟），期间面板暂时打不开；Minecraft 服务端不受影响。"
        confirmLabel="安装"
        danger={false}
        onCancel={() => setTarget(null)}
        onConfirm={() => {
          if (target) install.mutate(target);
          setTarget(null);
        }}
      />
    </Card>
  );
}
