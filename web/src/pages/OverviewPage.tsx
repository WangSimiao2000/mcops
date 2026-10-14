import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { ServerStatus } from '@/api/types';
import { useConsole } from '@/console/useConsole';
import { PageHeader } from '@/components/PageHeader';
import { ModuleHealthCard } from './overview/ModuleHealthCard';
import { PlayersCard } from './overview/PlayersCard';
import { RecentEventsCard } from './overview/RecentEventsCard';
import { SecurityOverviewCard } from './overview/SecurityOverviewCard';
import { ServerStatusCard } from './overview/ServerStatusCard';
import { HostStatsCard } from './overview/HostStatsCard';
import { SetupWizard } from './overview/SetupWizard';
import { FALLBACK_REFETCH_MS, GAUGE_REFETCH_MS } from '@/lib/live';

export function OverviewPage() {
  const qc = useQueryClient();
  const { lastStatus, lastEvent } = useConsole();

  const statusQuery = useQuery({
    queryKey: ['serverStatus'],
    queryFn: ({ signal }) => api.serverStatus(signal),
    // WS status frames overwrite this cache entry; the poll is only a backstop.
    refetchInterval: 30_000,
  });

  const systemQuery = useQuery({
    queryKey: ['system'],
    queryFn: ({ signal }) => api.system(signal),
    refetchInterval: GAUGE_REFETCH_MS,
  });

  const eventsQuery = useQuery({
    queryKey: ['events', 'overview'],
    queryFn: ({ signal }) => api.events({ limit: 20 }, signal),
    refetchInterval: FALLBACK_REFETCH_MS,
  });

  // When a WS status frame arrives, treat it as the freshest truth.
  useEffect(() => {
    if (lastStatus) {
      qc.setQueryData<ServerStatus>(['serverStatus'], lastStatus);
    }
  }, [lastStatus, qc]);

  // A live event invalidates the recent-events list.
  useEffect(() => {
    if (lastEvent) {
      void qc.invalidateQueries({ queryKey: ['events', 'overview'] });
    }
  }, [lastEvent, qc]);

  return (
    <div className="space-y-4">
      <PageHeader title="总览" subtitle="服务器状态、在线玩家、安全与模块健康一览" />
      {statusQuery.isError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法获取服务器状态，正在重试…
        </p>
      ) : null}
      <SetupWizard />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ServerStatusCard status={statusQuery.data ?? null} mode={systemQuery.data?.mode} />
        <PlayersCard status={statusQuery.data ?? null} />
      </div>
      <HostStatsCard />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SecurityOverviewCard />
        <RecentEventsCard events={eventsQuery.data?.items} isLoading={eventsQuery.isLoading} />
        <ModuleHealthCard system={systemQuery.data} />
      </div>
    </div>
  );
}
