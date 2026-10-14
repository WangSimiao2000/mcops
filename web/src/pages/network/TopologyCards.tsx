import type { IpTag, Overview } from '@/api/network';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatDateTime } from '@/lib/format';
import { TAG_LABEL, TAG_TONE, type Health, dotColor } from './constants';

export function TagBadges({ tags }: { tags: IpTag[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {tags.map((t) => (
        <Badge key={t} tone={TAG_TONE[t]}>
          {TAG_LABEL[t]}
        </Badge>
      ))}
    </span>
  );
}

function Hop({ label, health, detail }: { label: string; health: Health; detail: string }) {
  return (
    <div className="flex flex-col items-center gap-1" title={detail}>
      <span className={`h-3 w-3 rounded-full ${dotColor(health)}`} aria-label={detail} />
      <span className="text-xs text-slate-600 dark:text-slate-300">{label}</span>
    </div>
  );
}

export function TopologyCard({ data }: { data: Overview }) {
  const edgeHealth: Health = !data.edge.enabled ? 'warn' : data.edge.connected ? 'ok' : 'bad';
  const frpcHealth: Health =
    data.frpc.status === 'active' ? 'ok' : data.frpc.status === 'unknown' ? 'warn' : 'bad';
  const mcHealth: Health = data.mc.ok ? 'ok' : 'bad';
  return (
    <Card>
      <CardHeader title="链路拓扑" />
      <CardBody className="py-3">
        <div className="flex items-center justify-between gap-2">
          <Hop label="玩家" health="ok" detail="外部玩家" />
          <span className="h-px flex-1 bg-slate-300 dark:bg-slate-700" />
          <Hop
            label={data.frps_host ? `frps（${data.frps_host}）` : 'frps'}
            health={edgeHealth}
            detail={
              !data.edge.enabled
                ? '边缘采集未启用'
                : data.edge.connected
                  ? `已连接，最后事件 ${formatDateTime(data.edge.last_event_ts)}`
                  : `断开：${data.edge.error ?? '未知'}`
            }
          />
          <span className="h-px flex-1 bg-slate-300 dark:bg-slate-700" />
          <Hop
            label="frpc"
            health={frpcHealth}
            detail={`systemctl is-active frpc：${data.frpc.status}`}
          />
          <span className="h-px flex-1 bg-slate-300 dark:bg-slate-700" />
          <Hop
            label="MC"
            health={mcHealth}
            detail={
              data.mc.ok
                ? `在线 ${data.mc.online}/${data.mc.max}，延迟 ${data.mc.latency_ms}ms`
                : `SLP 自检失败：${data.mc.error ?? '未知'}`
            }
          />
        </div>
      </CardBody>
    </Card>
  );
}

export function PortsCard({ data }: { data: Overview }) {
  return (
    <Card>
      <CardHeader title="对外端口" />
      <CardBody>
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-slate-400">
              <th className="py-1">端口</th>
              <th className="py-1">用途</th>
            </tr>
          </thead>
          <tbody>
            {data.ports.map((p) => (
              <tr key={p.port} className="border-t border-slate-100 dark:border-slate-800">
                <td className="py-1 font-mono tabular-nums">{p.port}</td>
                <td className="py-1">{p.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

// ---- 统计 + 时间序列图 -----------------------------------------------------
