import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { networkApi, type NetWindow, type SeriesPoint, type Stats } from '@/api/network';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import {
  WINDOWS,
  WINDOW_LABEL,
  PROXY_FILL_CLASS,
  PROXY_FILL_FALLBACK,
  PROXY_TEXT_CLASS,
  PROXY_TEXT_FALLBACK,
  tzLabel,
} from './constants';

function BarChart({ series, proxies }: { series: SeriesPoint[]; proxies: string[] }) {
  const width = 560;
  const height = 190;
  const padL = 34;
  const padB = 26;
  const padT = 8;
  const max = useMemo(() => {
    let m = 0;
    for (const s of series) for (const p of proxies) m = Math.max(m, s.counts[p] ?? 0);
    return Math.max(m, 1);
  }, [series, proxies]);

  if (series.length === 0) {
    return <p className="py-6 text-center text-sm text-slate-400">该时间窗口内无连接</p>;
  }

  const chartW = width - padL;
  const chartH = height - padB - padT;
  const groupW = chartW / series.length;
  const barW = Math.max(2, (groupW * 0.7) / proxies.length);

  // Y 轴刻度（0, max/2, max）
  const ticks = [0, Math.round(max / 2), max];

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="mx-auto block max-h-72 w-full"
        role="img"
        aria-label="连接数时间序列"
      >
        {ticks.map((t) => {
          const y = padT + chartH - (t / max) * chartH;
          return (
            <g key={t}>
              <line x1={padL} y1={y} x2={width} y2={y} className="stroke-slate-200 dark:stroke-slate-800" />
              <text x={0} y={y + 3} className="fill-slate-500 text-[10px] tabular-nums dark:fill-slate-400">
                {t}
              </text>
            </g>
          );
        })}
        {series.map((s, i) => {
          const gx = padL + i * groupW;
          return (
            <g key={s.bucket}>
              {proxies.map((p, j) => {
                const v = s.counts[p] ?? 0;
                const h = (v / max) * chartH;
                const x = gx + groupW * 0.15 + j * barW;
                const y = padT + chartH - h;
                return (
                  <rect
                    key={p}
                    x={x}
                    y={y}
                    width={barW}
                    height={h}
                    className={PROXY_FILL_CLASS[p] ?? PROXY_FILL_FALLBACK}
                  >
                    <title>{`${s.bucket} · ${p}：${v}`}</title>
                  </rect>
                );
              })}
              {i % Math.ceil(series.length / 8) === 0 ? (
                <text
                  x={gx + groupW / 2}
                  y={height - 7}
                  textAnchor="middle"
                  className="fill-slate-500 text-[10px] dark:fill-slate-400"
                >
                  {s.bucket.slice(5)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex flex-wrap gap-4 text-xs">
        {proxies.map((p) => (
          <span key={p} className="flex items-center gap-1.5">
            <span
              className={
                'inline-block h-2.5 w-2.5 rounded-sm bg-current ' +
                (PROXY_TEXT_CLASS[p] ?? PROXY_TEXT_FALLBACK)
              }
            />
            <span className="text-slate-600 dark:text-slate-300">{p}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

export function StatsCard({ window, onWindow }: { window: NetWindow; onWindow: (w: NetWindow) => void }) {
  const query = useQuery({
    queryKey: ['network', 'stats', window],
    queryFn: ({ signal }) => networkApi.stats(window, signal),
    retry: false,
  });
  const data: Stats | undefined = query.data;
  return (
    <Card>
      <CardHeader
        title="连接统计"
        action={
          <div className="flex gap-1" role="group" aria-label="时间窗口">
            {WINDOWS.map((w) => (
              <Button
                key={w}
                size="sm"
                variant={w === window ? 'primary' : 'ghost'}
                onClick={() => onWindow(w)}
              >
                {WINDOW_LABEL[w]}
              </Button>
            ))}
          </div>
        }
      />
      <CardBody className="space-y-4">
        {query.isLoading ? (
          <p className="text-sm text-slate-400">加载中…</p>
        ) : data ? (
          <>
            <div className="flex flex-wrap gap-3">
              {data.proxies.map((p) => {
                const st = data.by_proxy[p] ?? { connections: 0, distinct_ips: 0 };
                return (
                  <div
                    key={p}
                    className="rounded-md border border-slate-200 px-3 py-2 text-sm dark:border-slate-800"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className={
                          'inline-block h-2.5 w-2.5 rounded-sm bg-current ' +
                          (PROXY_TEXT_CLASS[p] ?? PROXY_TEXT_FALLBACK)
                        }
                      />
                      <span className="font-medium">{p}</span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                      连接 <span className="tabular-nums font-semibold">{st.connections}</span> ·
                      独立 IP <span className="tabular-nums font-semibold">{st.distinct_ips}</span>
                    </div>
                  </div>
                );
              })}
            </div>
            <BarChart series={data.series} proxies={data.proxies} />
            {tzLabel(data.tz_offset_min) ? (
              <p className="mt-1 text-right text-[11px] text-slate-500 dark:text-slate-400">
                {tzLabel(data.tz_offset_min)}
              </p>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-red-600 dark:text-red-400">无法加载统计。</p>
        )}
      </CardBody>
    </Card>
  );
}
