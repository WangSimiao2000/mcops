import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import type { ServerStatus } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { formatUptime, SERVER_STATE_LABEL, serverStateTone } from '@/lib/format';
import { playersSummary } from '@/lib/players';
import { isControlDisabled, OBSERVE_NOTICE } from '@/lib/security';
import type { SystemMode } from '@/api/types';

type PendingAction = 'stop' | 'restart' | null;

export function ServerStatusCard({
  status,
  mode,
}: {
  status: ServerStatus | null;
  mode: SystemMode | undefined;
}) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<PendingAction>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const observe = isControlDisabled(mode);

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['serverStatus'] });

  const start = useMutation({
    mutationFn: () => api.serverStart(),
    onSuccess: invalidate,
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '操作失败'),
  });
  const stop = useMutation({
    mutationFn: () => api.serverStop(),
    onSuccess: invalidate,
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '操作失败'),
  });
  const restart = useMutation({
    mutationFn: () => api.serverRestart(),
    onSuccess: invalidate,
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '操作失败'),
  });

  const busy = start.isPending || stop.isPending || restart.isPending;
  const state = status?.state ?? 'stopped';

  function confirmAction() {
    if (pending === 'stop') stop.mutate();
    if (pending === 'restart') restart.mutate();
    setPending(null);
  }

  return (
    <Card>
      <CardHeader
        title="服务器状态"
        action={<Badge tone={serverStateTone(state)}>{SERVER_STATE_LABEL[state]}</Badge>}
      />
      <CardBody>
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-slate-500 dark:text-slate-400">版本</dt>
            <dd className="font-medium">{status?.mc_version ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">运行时长</dt>
            <dd className="font-medium">{formatUptime(status?.uptime_s ?? null)}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">PID</dt>
            <dd className="font-medium">{status?.pid ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">在线玩家</dt>
            <dd className="font-medium">
              {status?.players ? playersSummary(status.players) : '—'}
            </dd>
          </div>
        </dl>

        {actionError ? (
          <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
            {actionError}
          </p>
        ) : null}

        {observe ? (
          <p className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300">
            {OBSERVE_NOTICE}
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            size="sm"
            onClick={() => {
              setActionError(null);
              start.mutate();
            }}
            disabled={observe || busy || state === 'running' || state === 'starting'}
          >
            启动
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={() => {
              setActionError(null);
              setPending('stop');
            }}
            disabled={observe || busy || state === 'stopped'}
          >
            停止
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setActionError(null);
              setPending('restart');
            }}
            disabled={observe || busy || state === 'stopped'}
          >
            重启
          </Button>
        </div>
      </CardBody>

      <ConfirmDialog
        open={pending !== null}
        title={pending === 'stop' ? '停止服务器？' : '重启服务器？'}
        description={
          pending === 'stop'
            ? '所有在线玩家将被断开，世界会先保存。确认停止？'
            : '服务器将短暂离线并重新启动。确认重启？'
        }
        confirmLabel={pending === 'stop' ? '停止' : '重启'}
        onConfirm={confirmAction}
        onCancel={() => setPending(null)}
      />
    </Card>
  );
}
