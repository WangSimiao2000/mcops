import type { ConnectionState } from '@/api/consoleSocket';
import { Badge } from '@/components/ui/Badge';

const LABEL: Record<ConnectionState, string> = {
  connecting: '连接中',
  open: '已连接',
  closed: '已断开',
};

const TONE = {
  connecting: 'yellow',
  open: 'green',
  closed: 'red',
} as const;

export function ConnectionIndicator({ state }: { state: ConnectionState }) {
  return (
    <Badge tone={TONE[state]}>
      <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
      {LABEL[state]}
    </Badge>
  );
}
