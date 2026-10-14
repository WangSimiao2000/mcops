import { useId } from 'react';
import type { FieldModel } from '@/lib/schemaForm';
import { otherJvmOpts, readMemoryGb, writeMemoryGb } from '@/lib/schemaForm';

const inputCls =
  'w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand focus:ring-0 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100';

export interface FieldControlProps {
  field: FieldModel;
  value: unknown;
  onChange: (next: unknown) => void;
  /** 由外层 <label htmlFor> 提供，保证标签与输入框关联 */
  id?: string;
  describedBy?: string;
}

/**
 * 纯输入控件：按 kind + widget 选择。标签/徽标/「恢复默认」由调用方（ServerConfigTab）
 * 负责，这里只渲染值的编辑器。
 */
export function FieldControl({ field, value, onChange, id: idProp, describedBy }: FieldControlProps) {
  const autoId = useId();
  const id = idProp ?? autoId;

  // memory widget：编辑 java_opts 内的 -Xms/-Xmx（GB），其它参数保持不变。
  if (field.widget === 'memory') {
    return <MemoryControl id={id} describedBy={describedBy} value={Array.isArray(value) ? (value as string[]) : []} onChange={onChange} />;
  }

  if (field.widget === 'time') {
    return (
      <input
        id={id}
        aria-describedby={describedBy}
        type="time"
        className={inputCls}
        value={typeof value === 'string' ? value : ''}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (field.widget === 'lines' || field.kind === 'string-array') {
    const arr = Array.isArray(value) ? (value as string[]) : [];
    return (
      <textarea
        id={id}
        aria-describedby={describedBy}
        rows={Math.max(2, Math.min(8, arr.length + 1))}
        className={`${inputCls} font-mono`}
        value={arr.join('\n')}
        placeholder="每行一项"
        onChange={(e) =>
          onChange(
            e.target.value
              .split('\n')
              .map((s) => s.trim())
              .filter((s) => s.length > 0),
          )
        }
      />
    );
  }

  if (field.kind === 'boolean') {
    return (
      <label htmlFor={id} className="inline-flex items-center gap-2 text-sm">
        <input
          id={id}
          aria-describedby={describedBy}
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 rounded border-slate-300 text-brand focus:ring-0 dark:border-slate-600"
        />
        <span className="text-slate-600 dark:text-slate-300">{value === true ? '开' : '关'}</span>
      </label>
    );
  }

  if (field.kind === 'enum') {
    return (
      <select
        id={id}
        aria-describedby={describedBy}
        className={inputCls}
        value={value == null ? '' : String(value)}
        onChange={(e) => onChange(coerceEnum(e.target.value, field))}
      >
        {field.nullable ? <option value="">（未设置）</option> : null}
        {(field.enumValues ?? []).map((opt) => (
          <option key={String(opt)} value={String(opt)}>
            {String(opt)}
          </option>
        ))}
      </select>
    );
  }

  if (field.kind === 'int-tuple') {
    const len = field.tupleLen ?? 3;
    const arr = Array.from({ length: len }, (_, i) => (Array.isArray(value) ? (value as number[])[i] ?? 0 : 0));
    const labels = len === 3 ? ['X', 'Y', 'Z'] : arr.map((_, i) => String(i + 1));
    return (
      <div className="flex flex-wrap gap-2">
        {arr.map((v, i) => (
          <label key={i} className="flex items-center gap-1 text-sm">
            <span className="text-slate-500 dark:text-slate-400">{labels[i]}</span>
            <input
              id={i === 0 ? id : undefined}
              type="number"
              step={1}
              min={field.min}
              max={field.max}
              value={String(v)}
              aria-label={`${field.label} ${labels[i]}`}
              onChange={(e) => {
                const next = [...arr];
                next[i] = Math.round(Number(e.target.value) || 0);
                onChange(next);
              }}
              className="w-24 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-900 focus:border-brand focus:ring-0 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            />
          </label>
        ))}
      </div>
    );
  }

  if (field.kind === 'integer' || field.kind === 'number') {
    return (
      <input
        id={id}
        aria-describedby={describedBy}
        type="number"
        className={inputCls}
        value={value === '' || value == null ? '' : String(value)}
        min={field.min}
        max={field.max}
        step={field.kind === 'integer' ? 1 : 'any'}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === '') return onChange(field.nullable ? null : '');
          const n = Number(raw);
          onChange(Number.isNaN(n) ? raw : n);
        }}
      />
    );
  }

  // string / unknown
  return (
    <input
      id={id}
      aria-describedby={describedBy}
      type="text"
      className={inputCls}
      value={value == null ? '' : String(value)}
      placeholder={field.nullable ? '（留空）' : undefined}
      onChange={(e) => onChange(field.nullable && e.target.value === '' ? null : e.target.value)}
    />
  );
}

function coerceEnum(raw: string, field: FieldModel): string | number | null {
  if (raw === '' && field.nullable) return null;
  const match = (field.enumValues ?? []).find((v) => String(v) === raw);
  return typeof match === 'number' ? match : raw;
}

/** 内存滑块 + 数字输入（GB），编辑 -Xms/-Xmx，其它 JVM 参数展示为只读说明。 */
function MemoryControl({
  id,
  describedBy,
  value,
  onChange,
}: {
  id: string;
  describedBy?: string;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const gb = readMemoryGb(value) ?? 4;
  const shown = Math.round(gb);
  const others = otherJvmOpts(value);
  const setGb = (next: number) => {
    const clamped = Math.max(1, Math.min(64, Math.round(next)));
    onChange(writeMemoryGb(value, clamped));
  };
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <input
          id={id}
          aria-describedby={describedBy}
          type="range"
          min={1}
          max={32}
          step={1}
          value={Math.min(32, shown)}
          onChange={(e) => setGb(Number(e.target.value))}
          className="h-2 flex-1 cursor-pointer accent-brand"
          aria-label="内存 GB 滑块"
        />
        <div className="flex shrink-0 items-center gap-1">
          <input
            type="number"
            min={1}
            max={64}
            value={shown}
            onChange={(e) => setGb(Number(e.target.value))}
            className="w-20 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-900 focus:border-brand focus:ring-0 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            aria-label="内存 GB"
          />
          <span className="text-sm text-slate-500 dark:text-slate-400">GB</span>
        </div>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        -Xms/-Xmx 均设为 {shown}G
        {others.length ? (
          <>
            ；其它参数保持不变：<code className="font-mono">{others.join(' ')}</code>
          </>
        ) : null}
      </p>
    </div>
  );
}
