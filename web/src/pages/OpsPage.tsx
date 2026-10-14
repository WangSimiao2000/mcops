import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import type { Job, JobRun } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState } from '@/components/EmptyState';
import { formatDateTime, formatDurationBetween } from '@/lib/format';
import {
  formatCountdown,
  jobDisplayName,
  JOB_RUN_STATUS_LABEL,
  jobRunStatusTone,
} from '@/lib/ops';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { scrollBox } from '@/lib/scroll';
import { LiveJobPanel } from '@/pages/ops/LiveJobPanel';

const OBSERVE_HINT =
  '观测模式：定时任务与备份仍由旧脚本（crontab）负责，面板暂不接管。切换到控制模式后此页可用。';

/** A ticking clock so countdowns re-render every second without refetching. */
function useNowTick(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function JobCard({
  job,
  running,
  onRun,
  disabled,
  now,
}: {
  job: Job;
  running: string | null;
  onRun: (job: Job) => void;
  disabled: boolean;
  now: number;
}) {
  const isRunning = running === job.id;
  const countdown = formatCountdown(job.next_run, now);
  return (
    <Card>
      <CardHeader
        title={job.name}
        action={isRunning ? <Badge tone="yellow">运行中</Badge> : null}
      />
      <CardBody>
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-slate-500 dark:text-slate-400">计划</dt>
            <dd className="font-medium">{job.schedule ?? '按需'}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">下次运行</dt>
            <dd className="font-medium">
              {job.next_run ? (
                <>
                  {formatDateTime(job.next_run)}
                  {countdown ? (
                    <span className="ml-1 text-xs text-slate-500 dark:text-slate-400">
                      （{countdown}）
                    </span>
                  ) : null}
                </>
              ) : (
                '—'
              )}
            </dd>
          </div>
        </dl>
        <div className="mt-4">
          <Button
            size="sm"
            variant={job.id === 'scheduled_restart' ? 'danger' : 'primary'}
            onClick={() => onRun(job)}
            disabled={disabled || running !== null}
          >
            {isRunning ? '运行中…' : '立即运行'}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function HistoryRow({ run }: { run: JobRun }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(run.log) || Object.keys(run.result).length > 0;
  return (
    <>
      <tr
        className={
          'border-b border-slate-100 last:border-0 dark:border-slate-800' +
          (hasDetail ? ' cursor-pointer' : '')
        }
        onClick={() => hasDetail && setOpen((v) => !v)}
      >
        <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(run.started_at)}</td>
        <td className="px-4 py-2" title={run.job}>{jobDisplayName(run.job)}</td>
        <td className="px-4 py-2">{run.trigger}</td>
        <td className="px-4 py-2 whitespace-nowrap">
          {formatDurationBetween(run.started_at, run.finished_at)}
        </td>
        <td className="px-4 py-2">
          <Badge tone={jobRunStatusTone(run.status)}>{JOB_RUN_STATUS_LABEL[run.status]}</Badge>
          {hasDetail ? (
            <span className="ml-2 text-xs text-slate-400">{open ? '▲' : '▼'}</span>
          ) : null}
        </td>
      </tr>
      {open && hasDetail ? (
        <tr className="border-b border-slate-100 dark:border-slate-800">
          <td colSpan={5} className="bg-slate-50 px-4 py-3 dark:bg-slate-800/40">
            {run.log ? (
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-xs text-slate-700 dark:text-slate-300">
                {run.log}
              </pre>
            ) : null}
            {Object.keys(run.result).length > 0 ? (
              <pre className="mt-2 overflow-auto whitespace-pre-wrap break-all font-mono text-xs text-slate-500 dark:text-slate-400">
                {JSON.stringify(run.result, null, 2)}
              </pre>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function OpsPage() {
  const qc = useQueryClient();
  const now = useNowTick();
  const [confirmJob, setConfirmJob] = useState<Job | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const jobsQuery = useQuery({
    queryKey: ['jobs'],
    queryFn: ({ signal }) => api.jobs(signal),
    // 有任务在跑时每 3 秒轮询，否则 15 秒。
    refetchInterval: (q) => (q.state.data?.running ? 3000 : FALLBACK_REFETCH_MS),
    retry: false,
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['jobs'] });

  const runMut = useMutation({
    mutationFn: (job: string) => api.runJob(job),
    onSuccess: invalidate,
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '操作失败'),
  });

  const resetMut = useMutation({
    mutationFn: () => api.watchdogReset(),
    onSuccess: invalidate,
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '操作失败'),
  });

  // 观测模式：GET /api/jobs 返回 409。
  const observe = jobsQuery.error instanceof ApiError && jobsQuery.error.status === 409;

  function requestRun(job: Job) {
    setActionError(null);
    if (job.id === 'scheduled_restart') {
      setConfirmJob(job);
    } else {
      runMut.mutate(job.id);
    }
  }

  if (observe) {
    return (
      <div className="space-y-4">
        <PageHeader title="运维任务" />
        <Card>
          <CardBody>
            <p className="text-sm text-slate-600 dark:text-slate-300">{OBSERVE_HINT}</p>
          </CardBody>
        </Card>
      </div>
    );
  }

  const data = jobsQuery.data;
  const running = data?.running ?? null;
  const watchdog = data?.watchdog;
  const history = data?.history ?? [];

  return (
    <div className="space-y-4">
      <PageHeader title="运维任务" subtitle="定时重启、按需备份与看门狗状态" />

      {jobsQuery.isError && !observe ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法获取运维任务，正在重试…
        </p>
      ) : null}

      {actionError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {actionError}
        </p>
      ) : null}

      {running && data?.live && data.live.job === running ? (
        <LiveJobPanel live={data.live} name={jobDisplayName(running)} />
      ) : running ? (
        <p
          role="status"
          className="rounded-md border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:border-yellow-900/50 dark:bg-yellow-900/20 dark:text-yellow-300"
        >
          任务「{jobDisplayName(running)}」正在运行，页面每 3 秒自动刷新…
        </p>
      ) : null}

      {watchdog?.suspended ? (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-300 bg-red-50 px-4 py-3 dark:border-red-800 dark:bg-red-900/20"
        >
          <div className="text-sm text-red-700 dark:text-red-300">
            <strong>看门狗已熔断：自动重启被暂停。</strong>
            {' '}近期崩溃 {watchdog.recent_crashes} 次。排查后点击右侧按钮恢复自动重启。
          </div>
          <Button
            size="sm"
            variant="danger"
            disabled={resetMut.isPending}
            onClick={() => {
              setActionError(null);
              resetMut.mutate();
            }}
          >
            恢复自动重启
          </Button>
        </div>
      ) : watchdog ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          看门狗正常（近期崩溃 {watchdog.recent_crashes} 次）。
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {jobsQuery.isLoading ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>
        ) : (
          (data?.jobs ?? []).map((job) => (
            <JobCard
              key={job.id}
              job={job}
              running={running}
              onRun={requestRun}
              disabled={runMut.isPending}
              now={now}
            />
          ))
        )}
      </div>

      <Card>
        <CardHeader title={`运行历史（${history.length}）`} />
        <CardBody className="p-0">
          <div className={scrollBox('lg', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="运行历史">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                  <th className="px-4 py-2 font-medium">时间</th>
                  <th className="px-4 py-2 font-medium">任务</th>
                  <th className="px-4 py-2 font-medium">触发者</th>
                  <th className="px-4 py-2 font-medium">耗时</th>
                  <th className="px-4 py-2 font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-10 text-center">
                      <EmptyState hint="暂无运行记录，上方卡片点击「立即运行」即可触发任务。" />
                    </td>
                  </tr>
                ) : (
                  history.map((run) => <HistoryRow key={run.id} run={run} />)
                )}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>

      <ConfirmDialog
        open={confirmJob !== null}
        title="立即执行定时重启 + 冷备份？"
        description="这会触发游戏内倒计时、停服、冷备份后再启动，服务器将停服约 2 分钟，在线玩家会被断开。确认立即运行？"
        confirmLabel="立即运行"
        onConfirm={() => {
          if (confirmJob) runMut.mutate(confirmJob.id);
          setConfirmJob(null);
        }}
        onCancel={() => setConfirmJob(null)}
      />
    </div>
  );
}
