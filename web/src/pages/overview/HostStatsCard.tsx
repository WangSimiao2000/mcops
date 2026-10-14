import { useQuery } from '@tanstack/react-query';

import { systemApi, type HostPoint } from '@/api/system';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatRate, formatUptimeShort, usageTone } from '@/lib/host';
import { formatBytes } from '@/lib/ops';
import { cn } from '@/lib/cn';

const BAR: Record<ReturnType<typeof usageTone>, string> = {
  ok: 'bg-emerald-500 dark:bg-emerald-400',
  warn: 'bg-amber-500 dark:bg-amber-400',
  bad: 'bg-red-500 dark:bg-red-400',
};

function Bar({ pct }: { pct: number | null }) {
  const v = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
      role="presentation"
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-500', BAR[usageTone(pct)])}
        style={{ width: `${v}%` }}
      />
    </div>
  );
}

/** 最近一小时的迷你折线（只用于看趋势，不画坐标轴）。 */
function Spark({
  points,
  pick,
  max,
  label,
}: {
  points: HostPoint[];
  pick: (p: HostPoint) => number | null;
  max?: number;
  label: string;
}) {
  const vals = points.map(pick);
  const nums = vals.filter((v): v is number => v != null);
  if (nums.length < 2) return <div className="h-8" aria-hidden />;
  const top = (max ?? Math.max(...nums) * 1.15) || 1;
  const w = 120;
  const h = 32;
  const step = w / Math.max(1, vals.length - 1);
  const d = vals
    .map((v, i) =>
      v == null ? null : `${(i * step).toFixed(1)},${(h - (Math.min(v, top) / top) * h).toFixed(1)}`,
    )
    .filter(Boolean)
    .join(' ');
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="h-8 w-full text-brand"
      role="img"
      aria-label={`${label}最近一小时趋势`}
    >
      <polyline
        points={d}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function Tile({
  title,
  value,
  sub,
  children,
}: {
  title: string;
  value: string;
  sub?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <div className="text-xs text-slate-500 dark:text-slate-400">{title}</div>
      <div className="truncate text-lg font-semibold tabular-nums text-slate-900 dark:text-slate-100">
        {value}
      </div>
      {sub ? <div className="truncate text-xs text-slate-500 dark:text-slate-400">{sub}</div> : null}
      {children}
    </div>
  );
}

export function HostStatsCard() {
  const q = useQuery({
    queryKey: ['hostStats'],
    queryFn: ({ signal }) => systemApi.host(signal),
    refetchInterval: 10_000,
  });
  const c = q.data?.current;
  const hist = q.data?.history ?? [];

  const memPct = c && c.mem.total ? (c.mem.used / c.mem.total) * 100 : null;
  const swapPct = c && c.mem.swap_total ? (c.mem.swap_used / c.mem.swap_total) * 100 : null;
  const load1 = c?.cpu.load[0];

  return (
    <Card>
      <CardHeader title="主机资源" />
      <CardBody>
        {q.isError ? <p className="text-sm text-red-600 dark:text-red-400">无法获取主机资源</p> : null}
        {!c ? (
          q.isLoading ? (
            <p className="text-sm text-slate-500">加载中…</p>
          ) : null
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Tile
              title="CPU"
              value={c.cpu.pct == null ? '—' : `${c.cpu.pct.toFixed(0)}%`}
              sub={`${c.cpu.cores} 核 · 负载 ${c.cpu.load.map((x) => x.toFixed(2)).join(' / ')}${load1 != null && load1 > c.cpu.cores ? '（偏高）' : ''}`}
            >
              <Bar pct={c.cpu.pct} />
              <Spark points={hist} pick={(p) => p.cpu} max={100} label="CPU " />
            </Tile>

            <Tile
              title="内存"
              value={`${formatBytes(c.mem.used)} / ${formatBytes(c.mem.total)}`}
              sub={`可用 ${formatBytes(c.mem.available)}${c.mem.swap_total ? ` · 交换区已用 ${formatBytes(c.mem.swap_used)}（${swapPct?.toFixed(0)}%）` : ''}`}
            >
              <Bar pct={memPct} />
              <Spark points={hist} pick={(p) => p.mem} max={100} label="内存" />
            </Tile>

            <Tile
              title="Minecraft 进程"
              value={c.mc ? formatBytes(c.mc.rss) : '未运行'}
              sub={
                c.mc
                  ? `PID ${c.mc.pid} · CPU ${c.mc.cpu_pct == null ? '—' : `${c.mc.cpu_pct.toFixed(0)}%`}（单核 100%）`
                  : undefined
              }
            >
              {c.mc ? <Bar pct={c.mem.total ? (c.mc.rss / c.mem.total) * 100 : null} /> : null}
              <Spark points={hist} pick={(p) => p.mc_rss} label="MC 内存" />
            </Tile>

            <Tile
              title="网络"
              value={`↓ ${formatRate(c.net?.rx_bps)}`}
              sub={`↑ ${formatRate(c.net?.tx_bps)} · 已运行 ${formatUptimeShort(c.uptime_s)}`}
            >
              <Spark points={hist} pick={(p) => p.tx} label="上行" />
            </Tile>

            {c.disks.map((d) => {
              const pct = d.total ? (d.used / d.total) * 100 : null;
              return (
                <Tile
                  key={d.path}
                  title={`磁盘 · ${d.labels.join('、')}`}
                  value={`剩余 ${formatBytes(d.free)}`}
                  sub={`已用 ${formatBytes(d.used)} / ${formatBytes(d.total)}（${pct?.toFixed(0)}%）· ${d.path}`}
                >
                  <Bar pct={pct} />
                </Tile>
              );
            })}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
