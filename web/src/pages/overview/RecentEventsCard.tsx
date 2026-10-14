import type { AppEvent } from '@/api/types';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatTime } from '@/lib/format';
import { scrollBox } from '@/lib/scroll';

function str(data: Record<string, unknown>, key: string): string | null {
  const v = data[key];
  return v === undefined || v === null ? null : String(v);
}

function EventRow({ ev }: { ev: AppEvent }) {
  const name = str(ev.data, 'name');
  const ip = str(ev.data, 'ip');

  let summary: React.ReactNode;
  switch (ev.type) {
    case 'player_login':
      summary = (
        <span>
          <span className="font-medium">{name ?? '未知'}</span> 登录
          {ip ? (
            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 font-mono text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
              {ip}
            </span>
          ) : null}
        </span>
      );
      break;
    case 'player_leave':
      summary = (
        <span>
          <span className="font-medium">{name ?? '未知'}</span> 离开
          {str(ev.data, 'reason') ? (
            <span className="ml-2 text-xs text-slate-500">{str(ev.data, 'reason')}</span>
          ) : null}
        </span>
      );
      break;
    case 'chat':
      summary = (
        <span>
          <span className="font-medium">{name ?? '未知'}</span>: {str(ev.data, 'message')}
        </span>
      );
      break;
    case 'anticheat':
      summary = (
        <span className="text-red-600 dark:text-red-400">
          外挂特征 · {name ?? '未知'} · {str(ev.data, 'kind')}
        </span>
      );
      break;
    case 'ban_ip':
      summary = (
        <span className="text-red-600 dark:text-red-400">
          封禁 IP {str(ev.data, 'ip')} · {str(ev.data, 'reason')}
        </span>
      );
      break;
    default:
      summary = <span className="text-slate-500">{ev.type}</span>;
  }

  return (
    <li className="flex items-start gap-3 py-2 text-sm">
      <time className="shrink-0 font-mono text-xs text-slate-400" dateTime={ev.ts}>
        {formatTime(ev.ts)}
      </time>
      <div className="min-w-0">{summary}</div>
    </li>
  );
}

export function RecentEventsCard({
  events,
  isLoading,
}: {
  events: AppEvent[] | undefined;
  isLoading: boolean;
}) {
  return (
    <Card>
      <CardHeader title="最近事件" />
      <CardBody>
        {isLoading ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>
        ) : !events || events.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">暂无事件</p>
        ) : (
          <ul className={scrollBox('sm', 'divide-y divide-slate-100 dark:divide-slate-800')} tabIndex={0}>
            {events.map((ev) => (
              <EventRow key={ev.id} ev={ev} />
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
