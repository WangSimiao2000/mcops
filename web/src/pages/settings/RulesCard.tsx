import type { NotifySettings, Sev } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { severityTone } from '@/lib/security';
import { ruleOverrideOptions, SEV_LABEL, type NotifyForm } from '@/lib/notify';
import { scrollBox } from '@/lib/scroll';

export function RulesCard({
  form,
  setForm,
  data,
}: {
  form: NotifyForm;
  setForm: (fn: (f: NotifyForm) => NotifyForm) => void;
  data: NotifySettings;
}) {
  const setRule = (id: string, patch: Partial<{ enabled: boolean; severity: Sev | null }>) =>
    setForm((f) => ({
      ...f,
      rules: { ...f.rules, [id]: { ...f.rules[id]!, ...patch } },
    }));

  return (
    <Card>
      <CardHeader title="告警规则" />
      <CardBody className="p-0">
        <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="告警规则">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="px-4 py-2 font-medium">规则</th>
                <th className="px-4 py-2 font-medium">默认级别</th>
                <th className="px-4 py-2 font-medium">启用</th>
                <th className="px-4 py-2 font-medium">级别覆盖</th>
              </tr>
            </thead>
            <tbody>
              {data.rules.map((r) => {
                const row = form.rules[r.id] ?? { enabled: r.enabled, severity: r.severity };
                const off = r.unavailable ?? null;
                return (
                  <tr
                    key={r.id}
                    className="border-b border-slate-100 last:border-0 dark:border-slate-800"
                  >
                    <td className="px-4 py-2">
                      <div className="font-medium">{r.name}</div>
                      <div className="text-xs text-slate-500 dark:text-slate-400">
                        {r.description}
                      </div>
                      {off ? (
                        <div className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                          已自动停用：{off}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-2">
                      <Badge tone={severityTone(r.default_severity)}>
                        {SEV_LABEL[r.default_severity]}
                      </Badge>
                    </td>
                    <td className="px-4 py-2">
                      <input
                        type="checkbox"
                        aria-label={`启用 ${r.name}`}
                        checked={row.enabled && !off}
                        disabled={Boolean(off)}
                        title={off ? `已自动停用：${off}` : undefined}
                        onChange={(ev) => setRule(r.id, { enabled: ev.target.checked })}
                        className="h-4 w-4 rounded border-slate-300 text-brand focus:ring-0 dark:border-slate-600"
                      />
                    </td>
                    <td className="px-4 py-2">
                      <select
                        aria-label={`级别覆盖 ${r.name}`}
                        value={row.severity ?? ''}
                        disabled={!row.enabled || Boolean(off)}
                        onChange={(ev) =>
                          setRule(r.id, {
                            severity: ev.target.value === '' ? null : (ev.target.value as Sev),
                          })
                        }
                        className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-900 focus:border-brand focus:ring-0 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                      >
                        {ruleOverrideOptions().map((o) => (
                          <option key={o.value || 'default'} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

export function StatsCard({ stats }: { stats: NotifySettings['stats'] }) {
  return (
    <Card>
      <CardHeader title="统计" />
      <CardBody>
        <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-slate-500 dark:text-slate-400">本小时已发邮件</dt>
            <dd className="font-medium tabular-nums">{stats.email_sent_hour}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">限流丢弃</dt>
            <dd className="font-medium tabular-nums">{stats.email_dropped}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">今日 QQ 推送</dt>
            <dd className="font-medium tabular-nums">{stats.qq_sent_today}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">渠道状态</dt>
            <dd className="font-medium">
              {!stats.last_error.email && !stats.last_error.qq ? '正常' : '见下方错误'}
            </dd>
          </div>
          {stats.last_error.email ? (
            <div className="col-span-2 sm:col-span-4">
              <dt className="text-slate-500 dark:text-slate-400">邮件最近错误</dt>
              <dd className="font-medium text-red-600 dark:text-red-400">
                {stats.last_error.email}
              </dd>
            </div>
          ) : null}
          {stats.last_error.qq ? (
            <div className="col-span-2 sm:col-span-4">
              <dt className="text-slate-500 dark:text-slate-400">QQ 最近错误</dt>
              <dd className="font-medium text-red-600 dark:text-red-400">
                {stats.last_error.qq}
              </dd>
            </div>
          ) : null}
        </dl>
      </CardBody>
    </Card>
  );
}
