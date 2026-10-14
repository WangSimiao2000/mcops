import { useMemo } from 'react';
import type { NotifyKnownGroup, NotifySettings } from '@/api/types';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { formatDateTime } from '@/lib/format';
import type { NotifyForm } from '@/lib/notify';
import { SeverityPicker, Toggle, TestButton } from './controls';
import { scrollBox } from '@/lib/scroll';

export function QqCard({
  form,
  setForm,
  data,
  dirty,
}: {
  form: NotifyForm;
  setForm: (fn: (f: NotifyForm) => NotifyForm) => void;
  data: NotifySettings;
  dirty: boolean;
}) {
  const q = form.qq;
  const setQq = (patch: Partial<NotifyForm['qq']>) =>
    setForm((f) => ({ ...f, qq: { ...f.qq, ...patch } }));

  // 已知群 + 当前勾选但不在 known_groups 中的历史群，一并展示。
  const rows = useMemo(() => {
    const map = new Map<string, NotifyKnownGroup>();
    for (const g of data.known_groups) map.set(g.openid, g);
    for (const openid of Object.keys(q.groups)) {
      if (!map.has(openid)) {
        map.set(openid, {
          openid,
          openid_masked: openid.length > 8 ? `${openid.slice(0, 4)}...${openid.slice(-4)}` : openid,
          added_at: null,
          is_alert: true,
        });
      }
    }
    return [...map.values()];
  }, [data.known_groups, q.groups]);

  const toggleGroup = (openid: string, checked: boolean) =>
    setForm((f) => {
      const groups = { ...f.qq.groups };
      if (checked) groups[openid] = groups[openid] ?? '';
      else delete groups[openid];
      return { ...f, qq: { ...f.qq, groups } };
    });

  const setLabel = (openid: string, label: string) =>
    setForm((f) => ({ ...f, qq: { ...f.qq, groups: { ...f.qq.groups, [openid]: label } } }));

  const botWarn = !data.bot.enabled
    ? '机器人未启用，QQ 推送不会发出。'
    : !data.bot.connected
      ? '机器人已启用但未连接，QQ 推送可能失败。'
      : null;

  return (
    <Card>
      <CardHeader
        title="QQ"
        action={
          <Toggle
            id="qq-enabled"
            label={q.enabled ? '已启用' : '已停用'}
            checked={q.enabled}
            onChange={(v) => setQq({ enabled: v })}
          />
        }
      />
      <CardBody className="space-y-4">
        {botWarn ? (
          <p
            role="alert"
            className="rounded-md border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-800 dark:border-yellow-900/50 dark:bg-yellow-900/20 dark:text-yellow-300"
          >
            {botWarn}
          </p>
        ) : null}

        <div className="space-y-1">
          <span className="block text-sm font-medium text-slate-700 dark:text-slate-200">
            接收级别
          </span>
          <SeverityPicker
            idPrefix="qq"
            value={q.severities}
            onChange={(severities) => setQq({ severities })}
          />
        </div>

        <div className="space-y-2">
          <span className="block text-sm font-medium text-slate-700 dark:text-slate-200">
            告警群
          </span>
          <ul className={scrollBox('sm', 'divide-y divide-slate-100 dark:divide-slate-800')} tabIndex={0}>
            {rows.length === 0 ? (
              <li className="py-2 text-sm text-slate-500 dark:text-slate-400">
                暂无已知群。先在管理群里 @机器人 发消息，机器人即可记录该群。
              </li>
            ) : (
              rows.map((g) => {
                const checked = Object.prototype.hasOwnProperty.call(q.groups, g.openid);
                return (
                  <li key={g.openid} className="flex flex-wrap items-center gap-3 py-2">
                    <label className="inline-flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={checked}
                        aria-label={`告警群 ${g.openid_masked}`}
                        onChange={(ev) => toggleGroup(g.openid, ev.target.checked)}
                        className="h-4 w-4 rounded border-slate-300 text-brand focus:ring-0 dark:border-slate-600"
                      />
                      <span className="font-mono text-xs">{g.openid_masked}</span>
                    </label>
                    <span className="text-xs text-slate-400">
                      {g.added_at ? `加入于 ${formatDateTime(g.added_at)}` : '历史群'}
                    </span>
                    {checked ? (
                      <input
                        type="text"
                        value={q.groups[g.openid] ?? ''}
                        placeholder="备注（可选）"
                        aria-label={`备注 ${g.openid_masked}`}
                        onChange={(ev) => setLabel(g.openid, ev.target.value)}
                        className="ml-auto w-40 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand focus:ring-0 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                      />
                    ) : null}
                  </li>
                );
              })
            )}
          </ul>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            也可以在管理群里 @机器人 发送「设为告警群」/「取消告警群」。
          </p>
        </div>

        <TestButton channel="qq" disabled={!q.enabled} dirty={dirty} />
      </CardBody>
    </Card>
  );
}
