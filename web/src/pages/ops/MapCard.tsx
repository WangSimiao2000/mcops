import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import type { MapStatus } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatDateTime } from '@/lib/format';
import { FALLBACK_REFETCH_MS } from '@/lib/live';
import { changedRegions, durationLabel, triggerLabel } from '@/lib/map';
import { formatBytes } from '@/lib/ops';

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

export function MapCard() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ['map'],
    queryFn: ({ signal }) => api.mapStatus(signal),
    refetchInterval: (s) => (s.state.data?.running ? 3000 : FALLBACK_REFETCH_MS),
    retry: false,
  });
  const run = useMutation({
    mutationFn: () => api.runMap(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['map'] }),
    onError: (e) => setErr(e instanceof ApiError ? e.detail : '操作失败'),
  });
  const m: MapStatus | undefined = q.data;
  if (!m || !m.enabled) return null; // 没配置 map_publish 就不显示这张卡

  const last = m.last ?? null;
  const latest = m.history?.[0] ?? null;
  const failed = latest && !latest.ok ? latest : null;
  const render = last?.result?.render;
  const counts = render?.tiles ?? m.tiles?.counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const preview = m.has_preview ? `/api/map/preview?t=${encodeURIComponent(m.tiles?.generated_at ?? '')}` : null;

  return (
    <Card>
      <CardHeader
        title="世界地图"
        action={
          m.running ? (
            <Badge tone="yellow">{m.phase ?? '生成中'}</Badge>
          ) : failed ? (
            <Badge tone="red">上次失败</Badge>
          ) : last ? (
            <Badge tone="green">正常</Badge>
          ) : (
            <Badge>尚未生成</Badge>
          )
        }
      />
      <CardBody>
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <Stat label="生成方式">
                {m.mode === 'restart' ? '跟随每日重启' : `每天 ${m.schedule}`}
              </Stat>
              <Stat label={m.mode === 'restart' ? '兜底补跑' : '下次运行'}>
                {m.running ? '—' : m.next_run ? formatDateTime(m.next_run) : '—'}
              </Stat>
              <Stat label="上次生成">
                {last ? (
                  <>
                    {formatDateTime(last.finished_at)}
                    <span className="ml-1 text-xs text-slate-500 dark:text-slate-400">
                      （{triggerLabel(last.trigger)}，用时 {durationLabel(last.seconds)}）
                    </span>
                  </>
                ) : (
                  '—'
                )}
              </Stat>
              <Stat label="本次变化">
                {last ? `${changedRegions(last) ?? 0} / ${render?.regions ?? '—'} 个区域` : '—'}
              </Stat>
              <Stat label="瓦片">
                {total ? `${total} 张${render?.bytes ? `，${formatBytes(render.bytes)}` : ''}` : '—'}
              </Stat>
              <Stat label="高清区域">
                {counts['0'] !== undefined ? `${counts['0']} 张（常待 ≥ ${m.hot_minutes} 分钟）` : '—'}
              </Stat>
              <Stat label="群播报">{m.announce ? '有变化时播报' : '关闭'}</Stat>
              <Stat label="发布">{m.pushed ? '已推到 Pages' : '首次推送等待中'}</Stat>
            </dl>
            {failed ? (
              <p role="alert" className="mt-3 break-all text-sm text-red-600 dark:text-red-400">
                {formatDateTime(failed.finished_at)} 失败：{failed.error}
              </p>
            ) : null}
            {err ? (
              <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
                {err}
              </p>
            ) : null}
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button
                size="sm"
                onClick={() => {
                  setErr(null);
                  run.mutate();
                }}
                disabled={m.running || run.isPending}
              >
                {m.running ? '生成中…' : '立即生成'}
              </Button>
              {m.public_url ? (
                <a
                  className="text-sm text-blue-600 hover:underline dark:text-blue-400"
                  href={m.public_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  打开公开地图 ↗
                </a>
              ) : null}
            </div>
          </div>
          {preview ? (
            <a href={m.public_url ?? preview} target="_blank" rel="noopener noreferrer" className="block">
              <img
                src={preview}
                alt="地图全图缩略图"
                className="w-full rounded-md bg-slate-100 object-contain dark:bg-slate-800"
                loading="lazy"
              />
            </a>
          ) : null}
        </div>
        {m.history && m.history.length > 1 ? (
          <ul className="mt-4 space-y-1 text-xs text-slate-500 dark:text-slate-400" aria-label="最近生成记录">
            {m.history.slice(0, 7).map((h) => (
              <li key={h.started_at} className="flex flex-wrap gap-x-3">
                <span className="whitespace-nowrap">{formatDateTime(h.started_at)}</span>
                <span>{triggerLabel(h.trigger)}</span>
                <span>{durationLabel(h.seconds)}</span>
                {h.ok ? (
                  <span>变化 {changedRegions(h) ?? 0} 个区域</span>
                ) : (
                  <span className="text-red-600 dark:text-red-400">失败</span>
                )}
              </li>
            ))}
          </ul>
        ) : null}
      </CardBody>
    </Card>
  );
}
