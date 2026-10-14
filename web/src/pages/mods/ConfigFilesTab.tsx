import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { modStoreApi } from '@/api/modstore';
import { EmptyState } from '@/components/EmptyState';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { cn } from '@/lib/cn';
import { formatBytes } from '@/lib/ops';

import { errText, localSyntaxError } from './util';

export function ConfigFilesTab() {
  const [filter, setFilter] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ['modstore', 'config-files'],
    queryFn: () => modStoreApi.configFiles(),
  });

  const items = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return (list.data?.items ?? []).filter((i) => !f || i.path.toLowerCase().includes(f));
  }, [list.data, filter]);

  return (
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
      <Card className={cn(selected ? 'hidden lg:block' : '')}>
        <CardHeader title={`config/（${list.data?.items.length ?? 0}）`} />
        <CardBody className="space-y-2">
          <Input
            placeholder="筛选文件"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="筛选配置文件"
          />
          {list.isError ? (
            <p className="text-sm text-red-600 dark:text-red-400">{errText(list.error)}</p>
          ) : null}
          {list.data && items.length === 0 ? <EmptyState hint="没有可编辑的配置文件" /> : null}
          <ul className="max-h-[60vh] overflow-y-auto text-sm">
            {items.map((i) => (
              <li key={i.path}>
                <button
                  type="button"
                  disabled={!i.editable}
                  onClick={() => setSelected(i.path)}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left font-mono text-xs',
                    selected === i.path
                      ? 'bg-brand text-white'
                      : 'text-slate-700 hover:bg-slate-100 disabled:opacity-50 dark:text-slate-300 dark:hover:bg-slate-800',
                  )}
                  title={i.editable ? i.path : '超过 1 MB，不支持在线编辑'}
                >
                  <span className="truncate">{i.path}</span>
                  <span className={cn('shrink-0', selected === i.path ? 'text-white/80' : 'text-slate-400')}>
                    {formatBytes(i.size)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </CardBody>
      </Card>
      {selected ? (
        <Editor key={selected} path={selected} onBack={() => setSelected(null)} />
      ) : (
        <Card className="hidden lg:block">
          <CardBody>
            <EmptyState hint="从左侧选择一个配置文件来编辑。保存前会检查语法并备份原文件，改动在重启服务器后生效。" />
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function Editor({ path, onBack }: { path: string; onBack: () => void }) {
  const qc = useQueryClient();
  const file = useQuery({
    queryKey: ['modstore', 'config-file', path],
    queryFn: () => modStoreApi.configFile(path),
    staleTime: Infinity,
  });
  const [text, setText] = useState<string | null>(null);
  const [mtime, setMtime] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (file.data) {
      setText(file.data.content);
      setMtime(file.data.mtime);
    }
  }, [file.data]);

  const dirty = file.data != null && text != null && text !== file.data.content;
  const syntax = file.data && text != null ? localSyntaxError(file.data.format, text) : null;

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const save = useMutation({
    mutationFn: () => modStoreApi.saveConfigFile(path, text ?? '', mtime),
    onSuccess: (r) => {
      setMtime(r.mtime);
      qc.setQueryData(['modstore', 'config-file', path], {
        ...file.data,
        content: text,
        mtime: r.mtime,
      });
      void qc.invalidateQueries({ queryKey: ['mods'] });
      setMsg({ ok: true, text: '已保存，重启服务器后生效（原文件已备份）' });
    },
    onError: (e) => setMsg({ ok: false, text: errText(e) }),
  });

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <div className="flex min-w-0 items-center gap-2">
          <button type="button" className="text-sm text-brand lg:hidden" onClick={onBack}>
            ← 返回
          </button>
          <span className="truncate font-mono text-sm">{path}</span>
          {file.data ? <Badge tone="gray">{file.data.format.toUpperCase()}</Badge> : null}
          {dirty ? <Badge tone="yellow">未保存</Badge> : null}
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={!dirty || save.isPending}
            onClick={() => {
              setText(file.data?.content ?? '');
              setMsg(null);
            }}
          >
            撤销修改
          </Button>
          <Button
            size="sm"
            disabled={!dirty || syntax != null || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? '保存中…' : '保存'}
          </Button>
        </div>
      </div>
      <CardBody className="space-y-2">
        {file.isError ? (
          <p className="text-sm text-red-600 dark:text-red-400">{errText(file.error)}</p>
        ) : null}
        {text != null ? (
          <textarea
            aria-label={`编辑 ${path}`}
            spellCheck={false}
            className="h-[60vh] w-full resize-y rounded-md border border-slate-300 bg-slate-50 p-3 font-mono text-xs leading-5 text-slate-800 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setMsg(null);
            }}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
                if (dirty && !syntax && !save.isPending) save.mutate();
              }
            }}
          />
        ) : (
          <p className="text-sm text-slate-500">加载中…</p>
        )}
        {syntax ? <p className="text-sm text-red-600 dark:text-red-400">{syntax}</p> : null}
        {msg ? (
          <p
            className={cn(
              'text-sm',
              msg.ok ? 'text-green-700 dark:text-green-400' : 'text-red-600 dark:text-red-400',
            )}
          >
            {msg.text}
          </p>
        ) : null}
      </CardBody>
    </Card>
  );
}
