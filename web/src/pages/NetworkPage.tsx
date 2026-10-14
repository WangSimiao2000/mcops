import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import { networkApi, type NetWindow } from '@/api/network';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { PageHeader } from '@/components/PageHeader';
import { isControlDisabled, OBSERVE_NOTICE } from '@/lib/security';
import { featureOn, useCapabilities } from '@/lib/capabilities';
import { TopologyCard, PortsCard } from '@/pages/network/TopologyCards';
import { StatsCard } from '@/pages/network/StatsCard';
import { IpTable, SshCard, ScannerCard, BansCard } from '@/pages/network/IpCards';

// ---- 页面 ------------------------------------------------------------------
type Pending =
  | { kind: 'ban'; ip: string }
  | { kind: 'pardon'; ip: string }
  | { kind: 'ban-scanners' }
  | null;

export function NetworkPage() {
  const qc = useQueryClient();
  const { data: caps } = useCapabilities();
  const edge = featureOn(caps, 'edge');
  const [window, setWindow] = useState<NetWindow>('24h');
  const [pending, setPending] = useState<Pending>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const systemQuery = useQuery({
    queryKey: ['system'],
    queryFn: ({ signal }) => api.system(signal),
    retry: false,
  });
  const observe = isControlDisabled(systemQuery.data?.mode);

  const overviewQuery = useQuery({
    queryKey: ['network', 'overview'],
    queryFn: ({ signal }) => networkApi.overview(signal),
    refetchInterval: 60_000,
    retry: false,
  });

  function invalidateAll() {
    void qc.invalidateQueries({ queryKey: ['network'] });
  }

  const banMut = useMutation({
    mutationFn: (ip: string) => networkApi.ban(ip),
    onSuccess: invalidateAll,
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '封禁失败'),
  });
  const pardonMut = useMutation({
    mutationFn: (ip: string) => networkApi.pardon(ip),
    onSuccess: invalidateAll,
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '解封失败'),
  });
  const banScannersMut = useMutation({
    mutationFn: () => networkApi.banScanners(window),
    onSuccess: invalidateAll,
    onError: (e) => setActionError(e instanceof ApiError ? e.detail : '批量封禁失败'),
  });

  function confirm() {
    setActionError(null);
    if (pending?.kind === 'ban') banMut.mutate(pending.ip);
    else if (pending?.kind === 'pardon') pardonMut.mutate(pending.ip);
    else if (pending?.kind === 'ban-scanners') banScannersMut.mutate();
    setPending(null);
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="网络"
        subtitle="链路拓扑、连接统计、Top IP 与封禁管理"
        actions={
          observe ? (
            <span className="text-sm text-yellow-700 dark:text-yellow-400">{OBSERVE_NOTICE}</span>
          ) : undefined
        }
      />

      {actionError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {actionError}
        </p>
      ) : null}

      {edge && overviewQuery.data ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
          <TopologyCard data={overviewQuery.data} />
          <PortsCard data={overviewQuery.data} />
        </div>
      ) : edge && overviewQuery.isLoading ? (
        <p className="text-sm text-slate-400">加载链路状态…</p>
      ) : null}

      <StatsCard window={window} onWindow={setWindow} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
        <IpTable
          window={window}
          observe={observe}
          onBan={(ip) => setPending({ kind: 'ban', ip })}
          onPardon={(ip) => setPending({ kind: 'pardon', ip })}
        />
        <div className="space-y-4">
          <SshCard />
          <ScannerCard
            window={window}
            observe={observe}
            onBanAll={() => setPending({ kind: 'ban-scanners' })}
          />
          <BansCard observe={observe} onPardon={(ip) => setPending({ kind: 'pardon', ip })} />
        </div>
      </div>

      <ConfirmDialog
        open={pending !== null}
        title={
          pending?.kind === 'pardon'
            ? '解封 IP'
            : pending?.kind === 'ban-scanners'
              ? '批量封禁扫描器'
              : '封禁 IP'
        }
        description={
          pending?.kind === 'ban'
            ? `确认封禁 ${pending.ip}？将执行 ban-ip。`
            : pending?.kind === 'pardon'
              ? `确认解封 ${pending.ip}？将执行 pardon-ip。`
              : '确认封禁当前所有扫描器候选 IP？单次最多 50 个。'
        }
        confirmLabel={pending?.kind === 'pardon' ? '解封' : '封禁'}
        danger={pending?.kind !== 'pardon'}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}
