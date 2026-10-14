import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { PropItem } from '@/api/config';
import { ApiError } from '@/api/http';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';

const INPUT =
  'rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand focus:ring-0 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

const GROUP_ORDER = ['玩法', '世界', '玩家与权限', '网络与性能', '其它'];

type PropValue = string | number | boolean;

export function GameRulesTab() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ['properties'],
    queryFn: ({ signal }) => api.properties(signal),
    retry: false,
  });

  const [draft, setDraft] = useState<Record<string, PropValue>>({});
  const [error, setError] = useState<string | null>(null);
  const [backup, setBackup] = useState<string | null>(null);

  // 可见、非敏感的项（敏感键永不展示）。
  const shown = useMemo(() => (query.data?.items ?? []).filter((i) => !i.sensitive), [query.data?.items]);

  // 分组（已知键按 group，未知键归「高级（其它键）」）。
  const groups = useMemo(() => {
    const known = shown.filter((i) => i.known);
    const unknown = shown.filter((i) => !i.known);
    const byGroup = new Map<string, PropItem[]>();
    for (const it of known) {
      const g = it.group || '其它';
      if (!byGroup.has(g)) byGroup.set(g, []);
      byGroup.get(g)!.push(it);
    }
    const ordered = [...byGroup.entries()].sort(
      (a, b) => groupIndex(a[0]) - groupIndex(b[0]),
    );
    return { ordered, unknown };
  }, [shown]);

  const current = (item: PropItem): PropValue =>
    Object.prototype.hasOwnProperty.call(draft, item.key)
      ? draft[item.key]
      : (item.value as PropValue);

  const changedKeys = useMemo(() => {
    const out: string[] = [];
    for (const it of shown) {
      if (!Object.prototype.hasOwnProperty.call(draft, it.key)) continue;
      if (draft[it.key] !== (it.value as PropValue)) out.push(it.key);
    }
    return out;
  }, [draft, shown]);

  const setKey = (key: string, value: PropValue) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setError(null);
    setBackup(null);
  };

  const saveMut = useMutation({
    mutationFn: () => {
      const changes: Record<string, PropValue> = {};
      for (const k of changedKeys) changes[k] = draft[k];
      return api.updateProperties(changes);
    },
    onSuccess: (res) => {
      setBackup(res.backup);
      setDraft({});
      setError(null);
      void qc.invalidateQueries({ queryKey: ['properties'] });
    },
    onError: (e) => {
      setBackup(null);
      setError(e instanceof ApiError ? e.detail : '保存失败');
    },
  });

  if (query.isError) {
    const detail = query.error instanceof ApiError ? query.error.detail : '无法加载 server.properties';
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        {detail}
      </p>
    );
  }
  if (!query.data) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>;
  }

  const dirty = changedKeys.length > 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          直接编辑 <code className="font-mono text-xs">server.properties</code>，
          改动在下次开服生效。文件：
          <span className="font-mono text-xs break-all"> {query.data.path}</span>
        </p>
        <div className="flex items-center gap-2">
          {dirty ? <Badge tone="yellow">{changedKeys.length} 项未保存</Badge> : null}
          <Button
            size="sm"
            variant={dirty ? 'primary' : 'secondary'}
            disabled={!dirty || saveMut.isPending}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending ? '保存中…' : '保存'}
          </Button>
        </div>
      </div>

      {error ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}
      {backup ? (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          已保存，下次开服生效。原文件已备份为{' '}
          <span className="font-mono text-xs">{backup}</span>。
        </p>
      ) : null}

      {groups.ordered.map(([group, rows]) => (
        <Card key={group}>
          <CardHeader title={group} />
          <CardBody className="p-0">
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map((it) => (
                <PropRow
                  key={it.key}
                  item={it}
                  value={current(it)}
                  changed={changedKeys.includes(it.key)}
                  onChange={(v) => setKey(it.key, v)}
                />
              ))}
            </ul>
          </CardBody>
        </Card>
      ))}

      {groups.unknown.length > 0 ? (
        <Card>
          <CardHeader title="高级（其它键）" />
          <CardBody className="p-0">
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {groups.unknown.map((it) => (
                <PropRow
                  key={it.key}
                  item={it}
                  value={current(it)}
                  changed={changedKeys.includes(it.key)}
                  onChange={(v) => setKey(it.key, v)}
                />
              ))}
            </ul>
          </CardBody>
        </Card>
      ) : null}
    </div>
  );
}

function groupIndex(g: string): number {
  const i = GROUP_ORDER.indexOf(g);
  return i === -1 ? GROUP_ORDER.length : i;
}

function PropRow({
  item,
  value,
  changed,
  onChange,
}: {
  item: PropItem;
  value: PropValue;
  changed: boolean;
  onChange: (v: PropValue) => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
      <div className="min-w-[12rem] flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-slate-700 dark:text-slate-300">{item.key}</span>
          {item.title ? (
            <span className="text-sm text-slate-500 dark:text-slate-400">{item.title}</span>
          ) : null}
          {item.managed ? (
            <span title={item.managed_reason} className="cursor-help">
              <Badge tone="gray">mc-ops 管理</Badge>
            </span>
          ) : null}
          {changed ? <Badge tone="yellow">改动</Badge> : null}
        </div>
        {item.description ? (
          <p className="text-xs text-slate-400 dark:text-slate-500">{item.description}</p>
        ) : null}
        {item.managed && item.managed_reason ? (
          <p className="text-xs text-slate-400 dark:text-slate-500">{item.managed_reason}</p>
        ) : null}
      </div>
      <div className="shrink-0">
        <PropControl item={item} value={value} onChange={onChange} />
      </div>
    </li>
  );
}

function PropControl({
  item,
  value,
  onChange,
}: {
  item: PropItem;
  value: PropValue;
  onChange: (v: PropValue) => void;
}) {
  if (item.managed) {
    return (
      <span className="font-mono text-sm text-slate-400 dark:text-slate-500">
        {item.value === null ? '••••••' : String(item.value)}
      </span>
    );
  }
  if (item.type === 'bool') {
    return (
      <label className="inline-flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={Boolean(value)}
          aria-label={item.key}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 rounded border-slate-300 text-brand focus:ring-0 dark:border-slate-600"
        />
      </label>
    );
  }
  if (item.type === 'enum') {
    return (
      <select
        aria-label={item.key}
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
        className={INPUT}
      >
        {(item.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  }
  if (item.type === 'int') {
    return (
      <input
        type="number"
        aria-label={item.key}
        value={value === '' || value == null ? '' : Number(value)}
        min={item.min}
        max={item.max}
        step={1}
        onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        className={INPUT + ' w-32'}
      />
    );
  }
  return (
    <input
      type="text"
      aria-label={item.key}
      value={value == null ? '' : String(value)}
      onChange={(e) => onChange(e.target.value)}
      className={INPUT + ' w-48'}
    />
  );
}
