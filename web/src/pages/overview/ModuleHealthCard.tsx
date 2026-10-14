import type { SystemResponse } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { MODULE_STATE_LABEL, moduleStateTone } from '@/lib/format';

export function ModuleHealthCard({ system }: { system: SystemResponse | undefined }) {
  const modules = system ? Object.entries(system.modules) : [];
  return (
    <Card>
      <CardHeader title="模块健康" />
      <CardBody>
        {modules.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">暂无模块信息</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {modules.map(([name, m]) => (
              <li key={name} className="flex items-center justify-between py-2">
                <div className="min-w-0">
                  <span className="font-medium">{name}</span>
                  {m.detail ? (
                    <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">{m.detail}</span>
                  ) : null}
                  {m.restarts > 0 ? (
                    <span className="ml-2 text-xs text-slate-400">重启 {m.restarts} 次</span>
                  ) : null}
                </div>
                <Badge tone={moduleStateTone(m.state)}>{MODULE_STATE_LABEL[m.state]}</Badge>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
