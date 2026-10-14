import { useEffect, useId, useRef, useState } from 'react';
import type { RestoreScope, ResticSnapshot } from '@/api/types';
import { Button } from '@/components/ui/Button';
import { formatDateTime } from '@/lib/format';

const SCOPES: { key: RestoreScope; label: string; hint: string }[] = [
  {
    key: 'world',
    label: '只回退世界',
    hint: '地图、箱子、玩家背包和进度回到这个时间点；Mod、配置、白名单保持现在的。',
  },
  {
    key: 'game',
    label: '世界 + Mod + 配置',
    hint: '连同 mods/、config/、server.properties 和白名单、封禁名单一起回退。期间升级过游戏版本时不要选。',
  },
];

/** 从快照回退的确认框：选范围、说明后果，勾选知情后才能确认。 */
export function RestoreDialog({
  snapshot,
  repoName,
  warnMinutes,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  snapshot: ResticSnapshot;
  repoName: string;
  warnMinutes: number;
  pending: boolean;
  error: string | null;
  onConfirm: (scope: RestoreScope) => void;
  onCancel: () => void;
}) {
  const [scope, setScope] = useState<RestoreScope>('world');
  const [ack, setAck] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const groupName = useId();

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="presentation"
      onClick={onCancel}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-5 text-slate-700 shadow-xl dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={titleId} className="text-base font-semibold text-slate-900 dark:text-slate-100">
          回退到 {formatDateTime(snapshot.time)}
        </h2>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          来源：{repoName} · 快照 <span className="font-mono">{snapshot.id}</span>
        </p>

        <fieldset className="mt-4 space-y-2">
          <legend className="sr-only">回退范围</legend>
          {SCOPES.map((s) => (
            <label
              key={s.key}
              className="flex cursor-pointer gap-2 rounded-md border border-slate-200 p-2 text-sm dark:border-slate-700"
            >
              <input
                type="radio"
                name={groupName}
                value={s.key}
                checked={scope === s.key}
                onChange={() => setScope(s.key)}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium text-slate-900 dark:text-slate-100">{s.label}</span>
                <span className="block text-xs text-slate-500 dark:text-slate-400">{s.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-slate-600 dark:text-slate-400">
          <li>游戏内倒计时 {warnMinutes} 分钟后停服，回退完成自动开服，通常停服几分钟。</li>
          <li>这个时间点之后的进度会丢失。回退前会先给现状拍一个快照（标签 pre-restore），反悔可以再回退到它。</li>
          <li>如果回退后服务器开不起来，会自动换回现在的文件并开服。</li>
        </ul>

        <label className="mt-4 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          我知道这会停服，并丢失这个时间点之后的进度
        </label>

        {error ? (
          <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        ) : null}

        <div className="mt-5 flex justify-end gap-2">
          <Button ref={cancelRef} variant="secondary" onClick={onCancel}>
            取消
          </Button>
          <Button variant="danger" disabled={!ack || pending} onClick={() => onConfirm(scope)}>
            {pending ? '正在提交…' : '开始回退'}
          </Button>
        </div>
      </div>
    </div>
  );
}
