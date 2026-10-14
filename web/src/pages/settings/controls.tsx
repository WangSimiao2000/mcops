import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import type { NotifyChannel, Sev } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { severityTone } from '@/lib/security';
import { SEV_LABEL, SEVERITIES, toggleSeverity } from '@/lib/notify';

/** 一组级别多选复选框。 */
export function SeverityPicker({
  value,
  onChange,
  idPrefix,
  disabled,
}: {
  value: Sev[];
  onChange: (next: Sev[]) => void;
  idPrefix: string;
  disabled?: boolean;
}) {
  const set = new Set(value);
  return (
    <div className="flex flex-wrap gap-3" role="group" aria-label="接收级别">
      {SEVERITIES.map((sev) => (
        <label key={sev} className="inline-flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            id={`${idPrefix}-sev-${sev}`}
            checked={set.has(sev)}
            disabled={disabled}
            onChange={() => onChange(toggleSeverity(value, sev))}
            className="h-4 w-4 rounded border-slate-300 text-brand focus:ring-0 dark:border-slate-600"
          />
          <Badge tone={severityTone(sev)}>{SEV_LABEL[sev]}</Badge>
        </label>
      ))}
    </div>
  );
}

/** 启用/停用开关（复选框样式）。 */
export function Toggle({
  checked,
  onChange,
  label,
  id,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  id: string;
}) {
  return (
    <label htmlFor={id} className="inline-flex items-center gap-2 text-sm font-medium">
      <input
        type="checkbox"
        id={id}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-slate-300 text-brand focus:ring-0 dark:border-slate-600"
      />
      {label}
    </label>
  );
}

export function TestButton({
  channel,
  disabled,
  dirty,
}: {
  channel: NotifyChannel;
  disabled: boolean;
  dirty: boolean;
}) {
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const mut = useMutation({
    mutationFn: () => api.testNotify(channel),
    onSuccess: (r) => setResult(r),
    onError: (e) =>
      setResult({ ok: false, detail: e instanceof ApiError ? e.detail : '发送失败' }),
  });
  const label = channel === 'email' ? '发送测试邮件' : '发送测试消息';
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        size="sm"
        variant="secondary"
        disabled={disabled || dirty || mut.isPending}
        title={dirty ? '请先保存' : undefined}
        onClick={() => {
          setResult(null);
          mut.mutate();
        }}
      >
        {mut.isPending ? '发送中…' : label}
      </Button>
      {dirty ? (
        <span className="text-xs text-slate-500 dark:text-slate-400">请先保存后再测试</span>
      ) : result ? (
        <span
          role="status"
          className={
            'text-sm ' +
            (result.ok ? 'text-green-700 dark:text-green-400' : 'text-red-600 dark:text-red-400')
          }
        >
          {result.detail}
        </span>
      ) : null}
    </div>
  );
}
