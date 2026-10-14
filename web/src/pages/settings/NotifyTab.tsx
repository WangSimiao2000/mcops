import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import type { NotifySettings } from '@/api/types';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';
import { isDirty, toForm, toPayload, type NotifyForm } from '@/lib/notify';
import { EmailCard } from '@/pages/settings/EmailCard';
import { QqCard } from '@/pages/settings/QqCard';
import { RulesCard, StatsCard } from '@/pages/settings/RulesCard';

const INTRO =
  'Web 面板铃铛始终接收全部告警；下面两个渠道按级别额外推送。默认路由：严重 → QQ 管理群 + 邮件；警告 → 邮件。';

/** 通知设置（原「设置」页的全部内容，现作为「通知」标签页）。 */
export function NotifyTab({ showQq = true }: { showQq?: boolean }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<NotifyForm | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const query = useQuery({
    queryKey: ['settingsNotify'],
    queryFn: ({ signal }) => api.settingsNotify(signal),
    retry: false,
  });

  const data = query.data;

  useEffect(() => {
    if (data && form === null) {
      setForm(seedForm(data));
    }
  }, [data, form]);

  const saveMut = useMutation({
    mutationFn: () => {
      if (!form || !data) throw new Error('form not ready');
      return api.updateSettingsNotify(toPayload(form, data));
    },
    onSuccess: (fresh) => {
      qc.setQueryData(['settingsNotify'], fresh);
      setForm(seedForm(fresh));
      setSaveError(null);
      setSaved(true);
    },
    onError: (e) => {
      setSaved(false);
      setSaveError(e instanceof ApiError ? e.detail : '保存失败');
    },
  });

  const update = (fn: (f: NotifyForm) => NotifyForm) =>
    setForm((prev) => {
      if (!prev) return prev;
      setSaved(false);
      return fn(prev);
    });

  if (query.isError) {
    const detail = query.error instanceof ApiError ? query.error.detail : '无法加载通知设置';
    return (
      <Card>
        <CardBody>
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {detail}
          </p>
        </CardBody>
      </Card>
    );
  }

  if (!data || !form) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>;
  }

  const dirty = isDirty(form, data);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-end gap-3">
        {dirty ? (
          <>
            <span className="text-xs text-amber-600 dark:text-amber-400">有未保存修改</span>
            <Button
              size="sm"
              variant="ghost"
              disabled={saveMut.isPending}
              onClick={() => {
                setSaveError(null);
                setSaved(false);
                setForm(seedForm(data));
              }}
            >
              撤销修改
            </Button>
          </>
        ) : null}
        <Button
          size="sm"
          variant={dirty ? 'primary' : 'secondary'}
          disabled={!dirty || saveMut.isPending}
          onClick={() => {
            setSaved(false);
            saveMut.mutate();
          }}
        >
          {saveMut.isPending ? '保存中…' : '保存'}
        </Button>
      </div>

      <Card>
        <CardBody>
          <p className="text-sm text-slate-600 dark:text-slate-300">{INTRO}</p>
        </CardBody>
      </Card>

      {saveError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {saveError}
        </p>
      ) : null}
      {saved && !dirty ? (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          已保存。
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <EmailCard form={form} setForm={update} dirty={dirty} />
        {showQq ? <QqCard form={form} setForm={update} data={data} dirty={dirty} /> : null}
      </div>

      <RulesCard form={form} setForm={update} data={data} />
      <StatsCard stats={data.stats} />
    </div>
  );
}

function seedForm(data: NotifySettings): NotifyForm {
  const f = toForm(data);
  f.__hasPassword = data.email.has_password;
  return f;
}
