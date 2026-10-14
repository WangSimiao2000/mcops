import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState } from '@/components/EmptyState';
import { Tabs } from '@/components/Tabs';
import type { RestoreScope, ResticRepoId, ResticSnapshot } from '@/api/types';
import { RestoreDialog } from './RestoreDialog';
import { formatDateTime } from '@/lib/format';
import {
  backupDownloadUrl,
  estimateDownload,
  formatBytes,
  jobDisplayName,
  snapshotDownloadUrl,
  totalBackupSize,
} from '@/lib/ops';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { scrollBox } from '@/lib/scroll';

const OBSERVE_HINT =
  '观测模式：备份仍由旧脚本（crontab）负责，面板暂不接管。切换到控制模式后此页可用。';

export function BackupsPage() {
  const qc = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['backups'],
    queryFn: ({ signal }) => api.backups(signal),
    refetchInterval: FALLBACK_REFETCH_MS,
    retry: false,
  });

  const backupMut = useMutation({
    mutationFn: () => api.runJob('backup'),
    onSuccess: () => {
      // 备份是异步任务；稍等片刻后刷新列表（任务串行，通常几秒内落盘）。
      setTimeout(() => void qc.invalidateQueries({ queryKey: ['backups'] }), 1500);
    },
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '操作失败'),
  });

  const observe = query.error instanceof ApiError && query.error.status === 409;

  if (observe) {
    return (
      <div className="space-y-4">
        <PageHeader title="备份" />
        <Card>
          <CardBody>
            <p className="text-sm text-slate-600 dark:text-slate-300">{OBSERVE_HINT}</p>
          </CardBody>
        </Card>
      </div>
    );
  }

  const items = query.data?.items ?? [];
  const total = totalBackupSize(items);
  const rate = query.data?.download_rate_kib ?? 0;

  return (
    <div className="space-y-4">
      <PageHeader title="备份" subtitle="增量快照（restic，定时不停服）+ 完整打包（每天 05:00 冷备）" />

      <Card>
        <CardBody>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            两层备份：<strong>增量快照</strong>（restic，定时、不停服，只存变化部分）用于回到某个时间点，
            点快照右侧的「回退」即可（配置了异地副本时，也能从 NAS 上的快照回退）；
            <strong>完整打包</strong>（每天 05:00 停服冷备）作兜底。
          </p>
        </CardBody>
      </Card>

      {actionError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {actionError}
        </p>
      ) : null}

      {backupMut.isSuccess ? (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          已触发备份，稍后将出现在下方列表。
        </p>
      ) : null}

      {query.isError && !observe ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法获取备份列表，正在重试…
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <Button
          size="sm"
          disabled={backupMut.isPending}
          onClick={() => {
            setActionError(null);
            backupMut.mutate();
          }}
        >
          {backupMut.isPending ? '备份中…' : '立即备份（不停服）'}
        </Button>
      </div>

      <DownloadNote rate={rate} />

      <ResticSection rate={rate} />

      <Card>
        <CardHeader
          title={`完整打包（${items.length}）`}
          action={
            <span className="text-xs text-slate-500 dark:text-slate-400">
              总大小 {formatBytes(total)}
            </span>
          }
        />
        <CardBody className="p-0">
          <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="完整打包列表">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="px-4 py-2 font-medium">文件名</th>
                  <th className="px-4 py-2 text-right font-medium">大小</th>
                  <th className="px-4 py-2 font-medium">时间</th>
                  <th className="px-4 py-2 text-right font-medium">下载</th>
                </tr>
              </thead>
              <tbody>
                {query.isLoading ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-6 text-center text-slate-500 dark:text-slate-400">
                      加载中…
                    </td>
                  </tr>
                ) : items.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-10 text-center">
                      <EmptyState hint="暂无完整打包，点击上方「立即备份」生成第一个快照。" />
                    </td>
                  </tr>
                ) : (
                  items.map((b) => (
                    <tr
                      key={b.name}
                      className="border-b border-slate-100 last:border-0 dark:border-slate-800"
                    >
                      <td className="px-4 py-2 font-mono text-xs">{b.name}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{formatBytes(b.size)}</td>
                      <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(b.mtime)}</td>
                      <td className="px-4 py-2 text-right">
                        <DownloadLink
                          href={backupDownloadUrl(b.name)}
                          label={`下载 ${b.name}`}
                          eta={estimateDownload(b.size, rate)}
                        />
                      </td>
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

function ResticSection({ rate }: { rate: number }) {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [repo, setRepo] = useState<ResticRepoId>('local');
  const [target, setTarget] = useState<ResticSnapshot | null>(null);
  const [restoreErr, setRestoreErr] = useState<string | null>(null);
  const [restored, setRestored] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ['restic', repo],
    queryFn: ({ signal }) => api.restic(repo, signal),
    refetchInterval: FALLBACK_REFETCH_MS,
    retry: false,
  });
  const run = useMutation({
    mutationFn: () => api.runJob('restic_backup'),
    onSuccess: () => setTimeout(() => void qc.invalidateQueries({ queryKey: ['restic'] }), 15000),
    onError: (e) => setErr(e instanceof ApiError ? e.detail : '操作失败'),
  });
  const restore = useMutation({
    mutationFn: (v: { id: string; scope: RestoreScope }) => api.restoreSnapshot(v.id, repo, v.scope),
    onSuccess: (_r, v) => {
      setTarget(null);
      setRestored(v.id);
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
    onError: (e) => setRestoreErr(e instanceof ApiError ? e.detail : '操作失败'),
  });
  const info = q.data;
  if (!info || !info.enabled) return null;
  const snaps = info.snapshots;
  const repos = info.repos ?? [{ id: 'local' as const, name: '本机' }];
  const repoName = repos.find((r) => r.id === repo)?.name ?? repo;
  const busy = Boolean(info.restore_running);
  const isLocal = repo === 'local';

  return (
    <Card>
      <CardHeader
        title={`增量快照（${snaps.length}）`}
        action={
          <div className="flex items-center gap-3">
            {info.stats ? (
              <span className="text-xs text-slate-500 dark:text-slate-400">
                仓库占用 {formatBytes(info.stats.repo_bytes)}
              </span>
            ) : null}
            {isLocal ? (
              <Button
                size="sm"
                disabled={run.isPending}
                onClick={() => {
                  setErr(null);
                  run.mutate();
                }}
              >
                立即快照
              </Button>
            ) : null}
          </div>
        }
      />
      <CardBody className="p-0">
        {repos.length > 1 ? (
          <div className="px-4 pt-2">
            <Tabs
              tabs={repos.map((r) => ({ key: r.id, label: r.name }))}
              active={repo}
              onSelect={(k) => setRepo(k as ResticRepoId)}
              label="快照仓库"
            />
            {!isLocal ? (
              <p className="py-2 text-xs text-slate-500 dark:text-slate-400">
                异地副本里的快照，每次本机快照后自动复制过来。本机硬盘出问题时从这里回退。
              </p>
            ) : null}
          </div>
        ) : null}
        {err || info.error ? (
          <p role="alert" className="px-4 py-2 text-sm text-red-600 dark:text-red-400">
            {err ?? info.error}
          </p>
        ) : null}
        {run.isSuccess && isLocal ? (
          <p role="status" className="px-4 py-2 text-sm text-green-700 dark:text-green-400">
            已触发快照，完成后出现在下方。
          </p>
        ) : null}
        {restored ? (
          <p role="status" className="px-4 py-2 text-sm text-green-700 dark:text-green-400">
            已开始回退到快照 {restored}：游戏内倒计时后停服，进度见「运维任务」页。
          </p>
        ) : null}
        {busy ? (
          <p className="px-4 py-2 text-xs text-amber-700 dark:text-amber-400">
            任务「{jobDisplayName(info.restore_running ?? '')}」正在运行，暂时不能回退。
          </p>
        ) : null}
        {q.isFetching && !isLocal && snaps.length === 0 && !info.error ? (
          <p className="px-4 py-2 text-xs text-slate-500 dark:text-slate-400">正在读取{repoName}…</p>
        ) : null}
        <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="增量快照列表">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="hidden px-4 py-2 font-medium sm:table-cell">快照</th>
                <th className="px-4 py-2 font-medium">时间</th>
                <th className="hidden px-4 py-2 text-right font-medium sm:table-cell">本次新增</th>
                <th className="hidden px-4 py-2 text-right font-medium sm:table-cell">数据量</th>
                <th className="hidden px-4 py-2 font-medium md:table-cell">标签</th>
                <th className="px-4 py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {snaps.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-slate-500 dark:text-slate-400">
                    {isLocal ? '暂无快照（首次运行会在 agent 启动约 2 分钟后）' : '这个仓库里还没有快照'}
                  </td>
                </tr>
              ) : (
                snaps.map((s) => (
                  <tr key={s.id} className="border-b border-slate-100 last:border-0 dark:border-slate-800">
                    <td className="hidden px-4 py-2 font-mono text-xs sm:table-cell">{s.id}</td>
                    <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(s.time)}</td>
                    <td className="hidden px-4 py-2 text-right tabular-nums sm:table-cell">
                      {s.added_bytes == null ? '—' : formatBytes(s.added_bytes)}
                    </td>
                    <td className="hidden px-4 py-2 text-right tabular-nums sm:table-cell">
                      {s.total_bytes == null ? '—' : formatBytes(s.total_bytes)}
                    </td>
                    <td className="hidden px-4 py-2 text-xs text-slate-500 md:table-cell">{s.tags.join(', ')}</td>
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      {isLocal ? (
                        <DownloadLink
                          href={snapshotDownloadUrl(s.id)}
                          label={`下载快照 ${s.id}（zip）`}
                          eta={s.total_bytes == null ? null : estimateDownload(s.total_bytes, rate)}
                        />
                      ) : null}
                      <button
                        type="button"
                        disabled={busy}
                        aria-label={`回退到快照 ${s.id}`}
                        onClick={() => {
                          setRestoreErr(null);
                          setRestored(null);
                          setTarget(s);
                        }}
                        className="ml-1 inline-flex items-center rounded-md px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:text-red-400 dark:hover:bg-red-950/40"
                      >
                        回退
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </CardBody>
      {target ? (
        <RestoreDialog
          snapshot={target}
          repoName={repoName}
          warnMinutes={info.warn_minutes ?? 5}
          pending={restore.isPending}
          error={restoreErr}
          onConfirm={(scope) => restore.mutate({ id: target.id, scope })}
          onCancel={() => setTarget(null)}
        />
      ) : null}
    </Card>
  );
}

function DownloadNote({ rate }: { rate: number }) {
  return (
    <p className="text-xs text-slate-500 dark:text-slate-400">
      下载经云主机转发，和玩家共用带宽，所以{' '}
      {rate > 0 ? `限速 ${rate} KiB/s` : '当前不限速（可能影响玩家）'}，同一时间只能下载一个。
      完整打包支持断点续传；增量快照会现场导出为 zip（不支持续传，大小以实际为准）。限速可在「设置 →
      服务器配置 → 运维任务」里调整。
    </p>
  );
}

function DownloadLink({ href, label, eta }: { href: string; label: string; eta: string | null }) {
  return (
    <a
      href={href}
      download
      aria-label={label}
      title={eta ? `按当前限速预计 ${eta}` : undefined}
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium text-brand hover:bg-slate-100 dark:hover:bg-slate-800"
    >
      下载{eta ? <span className="text-slate-400">（{eta}）</span> : null}
    </a>
  );
}
