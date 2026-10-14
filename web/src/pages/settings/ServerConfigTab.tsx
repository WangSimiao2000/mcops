import { useId, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { ConfigResponse } from '@/api/config';
import { ApiError } from '@/api/http';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import {
  APPLY_LABEL,
  APPLY_TONE,
  buildSections,
  changedPaths,
  getAtPath,
  setAtPath,
  validateField,
  type FieldModel,
  type SectionModel,
} from '@/lib/schemaForm';
import { useCapabilities } from '@/lib/capabilities';
import { FieldControl } from './configControls';

// features.X=false 时隐藏对应 section。
const SECTION_FEATURE_GATE: Record<string, 'bot' | 'edge'> = {
  bot: 'bot',
  edge: 'edge',
};

export function ServerConfigTab() {
  const query = useQuery({
    queryKey: ['config'],
    queryFn: ({ signal }) => api.config(signal),
    retry: false,
  });
  const { data: caps } = useCapabilities();

  const sections = useMemo(() => {
    if (!query.data) return [];
    return buildSections(query.data.schema, query.data.sections);
  }, [query.data]);

  if (query.isError) {
    const detail = query.error instanceof ApiError ? query.error.detail : '无法加载配置';
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        {detail}
      </p>
    );
  }
  if (!query.data) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>;
  }

  const data = query.data;
  const visible = sections.filter((s) => {
    const gate = SECTION_FEATURE_GATE[s.key];
    if (!gate || !caps) return true;
    return caps.features[gate] !== false;
  });

  return (
    <div className="space-y-4">
      <ReadonlyCard readonly={data.readonly} />
      {visible.map((section) => (
        <SectionCard key={section.key} section={section} data={data} />
      ))}
    </div>
  );
}

function ReadonlyCard({ readonly }: { readonly: ConfigResponse['readonly'] }) {
  const rows: Array<[string, string]> = [];
  if (readonly.mode) rows.push(['运行模式', readonly.mode]);
  if (readonly.api) rows.push(['面板地址', readonly.api]);
  if (readonly.data_dir) rows.push(['数据目录', readonly.data_dir]);
  if (readonly.config_file) rows.push(['配置文件', readonly.config_file]);
  if (rows.length === 0) return null;
  return (
    <Card>
      <CardHeader title="运行信息（只读）" />
      <CardBody>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {rows.map(([k, v]) => (
            <div key={k} className="flex flex-wrap gap-2">
              <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
              <dd className="font-mono text-xs text-slate-700 dark:text-slate-300 break-all">{v}</dd>
            </div>
          ))}
        </dl>
      </CardBody>
    </Card>
  );
}

function SectionCard({ section, data }: { section: SectionModel; data: ConfigResponse }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Record<string, unknown>>(() => structuredClone(data.values));
  const [note, setNote] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // data.values 变化（保存/重启后刷新）时同步 draft。
  const valuesKey = JSON.stringify(getAtPath(data.values, section.key));
  const [lastKey, setLastKey] = useState(valuesKey);
  if (valuesKey !== lastKey) {
    setLastKey(valuesKey);
    setDraft(structuredClone(data.values));
    setSaved(false);
  }

  const leafFields = section.fields.filter((f) => f.kind !== 'object');
  const dirtyPaths = changedPaths(leafFields, data.values, draft);
  const dirty = dirtyPaths.length > 0;

  const setField = (path: string, value: unknown) => {
    setDraft((d) => setAtPath(d, path, value));
    setSaved(false);
    setError(null);
  };

  const resetMut = useMutation({
    mutationFn: (path: string) => api.resetConfig([path]),
    onSuccess: (res) => {
      qc.setQueryData<ConfigResponse>(['config'], (old) =>
        old ? { ...old, values: res.values, overridden: res.overridden, pending: res.pending } : old,
      );
    },
  });

  const saveMut = useMutation({
    mutationFn: () => {
      // 只提交本 section、仅有改动的叶子。
      let payload: Record<string, unknown> = {};
      for (const p of dirtyPaths) payload = setAtPath(payload, p, getAtPath(draft, p));
      return api.updateConfig(payload, note);
    },
    onSuccess: (res) => {
      qc.setQueryData<ConfigResponse>(['config'], (old) =>
        old ? { ...old, values: res.values, overridden: res.overridden, pending: res.pending } : old,
      );
      void qc.invalidateQueries({ queryKey: ['config'] });
      void qc.invalidateQueries({ queryKey: ['configHistory'] });
      setNote('');
      setError(null);
      setSaved(true);
    },
    onError: (e) => {
      setSaved(false);
      setError(e instanceof ApiError ? e.detail : '保存失败');
    },
  });

  const validate = (): string | null => {
    for (const f of leafFields) {
      const err = validateField(f, getAtPath(draft, f.path));
      if (err) return err;
    }
    return null;
  };

  const onSave = () => {
    const err = validate();
    if (err) {
      setError(err);
      return;
    }
    setSaved(false);
    saveMut.mutate();
  };

  const basicFields = leafFields.filter((f) => !f.advanced);
  const advancedFields = leafFields.filter((f) => f.advanced);

  return (
    <Card>
      <CardHeader
        title={section.title}
        action={
          dirty ? (
            <Badge tone="yellow">{dirtyPaths.length} 项未保存</Badge>
          ) : saved ? (
            <Badge tone="green">已保存</Badge>
          ) : null
        }
      />
      <CardBody className="space-y-4">
        {section.description ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">{section.description}</p>
        ) : null}

        <div className="space-y-4">
          {basicFields.map((f) => (
            <FieldRow
              key={f.path}
              field={f}
              value={getAtPath(draft, f.path)}
              overridden={data.overridden.includes(f.path)}
              onChange={(v) => setField(f.path, v)}
              onReset={() => resetMut.mutate(f.path)}
              resetting={resetMut.isPending}
            />
          ))}
        </div>

        {advancedFields.length > 0 ? (
          <div className="rounded-md border border-slate-200 dark:border-slate-800">
            <button
              type="button"
              className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium text-slate-700 dark:text-slate-300"
              aria-expanded={showAdvanced}
              onClick={() => setShowAdvanced((v) => !v)}
            >
              <span>高级（{advancedFields.length}）</span>
              <span aria-hidden="true">{showAdvanced ? '▾' : '▸'}</span>
            </button>
            {showAdvanced ? (
              <div className="space-y-4 border-t border-slate-200 px-3 py-3 dark:border-slate-800">
                {advancedFields.map((f) => (
                  <FieldRow
                    key={f.path}
                    field={f}
                    value={getAtPath(draft, f.path)}
                    overridden={data.overridden.includes(f.path)}
                    onChange={(v) => setField(f.path, v)}
                    onReset={() => resetMut.mutate(f.path)}
                    resetting={resetMut.isPending}
                  />
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        ) : null}

        <div className="flex flex-wrap items-end gap-3 border-t border-slate-200 pt-3 dark:border-slate-800">
          <label className="flex-1 min-w-[12rem] text-sm">
            <span className="mb-1 block text-slate-500 dark:text-slate-400">备注（可选）</span>
            <input
              type="text"
              value={note}
              placeholder="这次改了什么 / 为什么"
              onChange={(e) => setNote(e.target.value)}
              className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand focus:ring-0 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            />
          </label>
          <Button
            size="sm"
            variant={dirty ? 'primary' : 'secondary'}
            disabled={!dirty || saveMut.isPending}
            onClick={onSave}
          >
            {saveMut.isPending ? '保存中…' : '保存'}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function FieldRow({
  field,
  value,
  overridden,
  onChange,
  onReset,
  resetting,
}: {
  field: FieldModel;
  value: unknown;
  overridden: boolean;
  onChange: (v: unknown) => void;
  onReset: () => void;
  resetting: boolean;
}) {
  const id = useId();
  const descId = field.description ? `${id}-desc` : undefined;
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <label htmlFor={id} className="text-sm font-medium text-slate-800 dark:text-slate-200">
          {field.label}
        </label>
        {field.unit ? (
          <span className="text-xs text-slate-400 dark:text-slate-500">（{field.unit}）</span>
        ) : null}
        {field.apply ? (
          <Badge tone={APPLY_TONE[field.apply]}>{APPLY_LABEL[field.apply]}</Badge>
        ) : null}
        {overridden ? (
          <>
            <Badge tone="blue">已自定义</Badge>
            <button
              type="button"
              className="text-xs text-brand hover:underline disabled:opacity-50"
              disabled={resetting}
              onClick={onReset}
            >
              恢复默认
            </button>
          </>
        ) : null}
      </div>
      {field.description ? (
        <p id={descId} className="text-xs text-slate-500 dark:text-slate-400">
          {field.description}
        </p>
      ) : null}
      <FieldControl id={id} describedBy={descId} field={field} value={value} onChange={onChange} />
    </div>
  );
}
