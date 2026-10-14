import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import {
  modStoreApi,
  type InstallPlan,
  type InstallRequest,
  type SearchHit,
  type SearchIndex,
} from '@/api/modstore';
import { EmptyState } from '@/components/EmptyState';
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { formatBytes } from '@/lib/ops';

import { errText } from './util';

const INDEXES: { key: SearchIndex; label: string }[] = [
  { key: 'relevance', label: '相关度' },
  { key: 'downloads', label: '下载量' },
  { key: 'updated', label: '最近更新' },
  { key: 'newest', label: '最新发布' },
];

const STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  install: { label: '将安装', tone: 'blue' },
  installed: { label: '已安装', tone: 'green' },
  unavailable: { label: '无兼容版本', tone: 'red' },
  client_only: { label: '纯客户端，跳过', tone: 'gray' },
};

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function InstallTab() {
  const [q, setQ] = useState('');
  const [index, setIndex] = useState<SearchIndex>('relevance');
  const [offset, setOffset] = useState(0);
  const [anyVersion, setAnyVersion] = useState(false);
  const [target, setTarget] = useState<SearchHit | null>(null);
  const query = useDebounced(q, 350);

  useEffect(() => setOffset(0), [query, index, anyVersion]);

  const search = useQuery({
    queryKey: ['modstore', 'search', query, index, offset, anyVersion],
    queryFn: ({ signal }) => modStoreApi.search(query, { offset, index, anyVersion }, signal),
    staleTime: 60_000,
  });
  const data = search.data;

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="min-w-[14rem] flex-1 text-sm">
              <span className="mb-1 block text-slate-600 dark:text-slate-300">搜索 Modrinth</span>
              <Input
                type="search"
                placeholder="例如 lithium、chunky、性能优化"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-slate-600 dark:text-slate-300">排序</span>
              <select
                className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                value={index}
                onChange={(e) => setIndex(e.target.value as SearchIndex)}
              >
                {INDEXES.map((i) => (
                  <option key={i.key} value={i.key}>
                    {i.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
            <span>
              只显示 Fabric、服务端可用的 mod
              {data?.mc_version && !anyVersion ? `，且支持 Minecraft ${data.mc_version}` : ''}。
            </span>
            <label className="inline-flex items-center gap-1.5">
              <input type="checkbox" checked={anyVersion} onChange={(e) => setAnyVersion(e.target.checked)} />
              也显示不支持当前版本的
            </label>
          </div>
        </CardBody>
      </Card>

      {search.isError ? (
        <Card>
          <CardBody className="text-sm text-red-600 dark:text-red-400">{errText(search.error)}</CardBody>
        </Card>
      ) : null}

      {data && data.hits.length === 0 ? (
        <Card>
          <CardBody>
            <EmptyState hint="没有找到匹配的 mod，换个关键词试试（Modrinth 用英文名搜索更准）" />
          </CardBody>
        </Card>
      ) : null}

      <ul className="grid gap-3 md:grid-cols-2" aria-busy={search.isFetching}>
        {(data?.hits ?? []).map((h) => (
          <li key={h.project_id}>
            <Card className="h-full">
              <CardBody className="flex h-full gap-3">
                {h.icon_url ? (
                  <img
                    src={h.icon_url}
                    alt=""
                    className="h-12 w-12 shrink-0 rounded-md bg-slate-100 object-cover dark:bg-slate-800"
                    loading="lazy"
                    referrerPolicy="no-referrer"
                  />
                ) : (
                  <div
                    className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-slate-100 text-lg font-semibold text-slate-400 dark:bg-slate-800 dark:text-slate-500"
                    aria-hidden
                  >
                    {h.title.slice(0, 1)}
                  </div>
                )}
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <a
                      href={`https://modrinth.com/mod/${h.slug}`}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="truncate font-medium text-slate-900 hover:underline dark:text-slate-100"
                    >
                      {h.title}
                    </a>
                    {h.installed_file ? <Badge tone="green">已安装</Badge> : null}
                    {h.server_side === 'required' ? <Badge tone="gray">服务端必需</Badge> : null}
                  </div>
                  <p className="line-clamp-2 text-sm text-slate-600 dark:text-slate-300">{h.description}</p>
                  <div className="mt-auto flex items-center justify-between gap-2 pt-1 text-xs text-slate-500 dark:text-slate-400">
                    <span>
                      {h.author} · {compact(h.downloads)} 次下载
                    </span>
                    <Button
                      size="sm"
                      variant={h.installed_file ? 'secondary' : 'primary'}
                      onClick={() => setTarget(h)}
                    >
                      {h.installed_file ? '详情' : '安装'}
                    </Button>
                  </div>
                </div>
              </CardBody>
            </Card>
          </li>
        ))}
      </ul>

      {data && data.total > data.limit ? (
        <div className="flex items-center justify-center gap-3 text-sm">
          <Button
            size="sm"
            variant="secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - data.limit))}
          >
            上一页
          </Button>
          <span className="text-slate-500 dark:text-slate-400">
            {offset + 1}–{Math.min(offset + data.limit, data.total)} / {data.total}
          </span>
          <Button
            size="sm"
            variant="secondary"
            disabled={offset + data.limit >= data.total}
            onClick={() => setOffset(offset + data.limit)}
          >
            下一页
          </Button>
        </div>
      ) : null}

      {target ? <InstallDialog hit={target} onClose={() => setTarget(null)} /> : null}
    </div>
  );
}

function InstallDialog({ hit, onClose }: { hit: SearchHit; onClose: () => void }) {
  const qc = useQueryClient();
  const [versionId, setVersionId] = useState<string>('');
  const [done, setDone] = useState<string[] | null>(null);
  const req: InstallRequest[] = [{ project_id: hit.project_id, version_id: versionId || null }];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const versions = useQuery({
    queryKey: ['modstore', 'versions', hit.project_id],
    queryFn: () => modStoreApi.versions(hit.project_id),
  });
  const plan = useQuery({
    queryKey: ['modstore', 'plan', hit.project_id, versionId],
    queryFn: () => modStoreApi.plan(req),
    retry: false,
  });
  const install = useMutation({
    mutationFn: () => modStoreApi.install(req),
    onSuccess: (r) => {
      setDone(r.installed);
      void qc.invalidateQueries({ queryKey: ['mods'] });
      void qc.invalidateQueries({ queryKey: ['modstore', 'search'] });
    },
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="install-title"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-t-xl bg-white text-slate-900 shadow-xl dark:bg-slate-900 dark:text-slate-100 sm:rounded-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <h2 id="install-title" className="text-base font-semibold">
            安装 {hit.title}
          </h2>
          <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{hit.description}</p>
        </div>
        <div className="space-y-4 px-5 py-4">
          {done ? (
            <div className="space-y-2 text-sm">
              <p className="font-medium text-green-700 dark:text-green-400">
                已安装 {done.length} 个文件，重启服务器后生效。
              </p>
              <ul className="list-inside list-disc text-slate-600 dark:text-slate-300">
                {done.map((f) => (
                  <li key={f} className="font-mono text-xs">
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <>
              <label className="block text-sm">
                <span className="mb-1 block text-slate-600 dark:text-slate-300">版本</span>
                <select
                  className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900"
                  value={versionId}
                  onChange={(e) => setVersionId(e.target.value)}
                  disabled={versions.isLoading}
                >
                  <option value="">自动选择（最新正式版）</option>
                  {(versions.data?.items ?? []).map((v) => (
                    <option key={v.version_id} value={v.version_id}>
                      {v.version_number}
                      {v.version_type !== 'release' ? `（${v.version_type}）` : ''} ·{' '}
                      {v.date_published.slice(0, 10)}
                    </option>
                  ))}
                </select>
              </label>

              <div>
                <h3 className="mb-2 text-sm font-medium">安装清单（含必需依赖）</h3>
                {plan.isLoading ? <p className="text-sm text-slate-500">正在解析依赖…</p> : null}
                {plan.isError ? (
                  <p className="text-sm text-red-600 dark:text-red-400">{errText(plan.error)}</p>
                ) : null}
                {plan.data ? <PlanList plan={plan.data} /> : null}
              </div>
              {install.isError ? (
                <p className="text-sm text-red-600 dark:text-red-400">{errText(install.error)}</p>
              ) : null}
            </>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-slate-200 px-5 py-3 dark:border-slate-800">
          <Button variant="secondary" onClick={onClose}>
            {done ? '关闭' : '取消'}
          </Button>
          {!done ? (
            <Button
              disabled={!plan.data || plan.data.install_count === 0 || install.isPending}
              onClick={() => install.mutate()}
            >
              {install.isPending ? '安装中…' : `安装 ${plan.data?.install_count ?? ''} 个`}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function PlanList({ plan }: { plan: InstallPlan }) {
  return (
    <div className="space-y-2">
      <ul className="divide-y divide-slate-100 rounded-md border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
        {plan.items.map((i) => {
          const st = STATUS[i.status] ?? {
            label: i.status,
            tone: 'gray' as BadgeTone,
          };
          return (
            <li key={i.project_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <div className="min-w-0">
                <span className="font-medium">{i.title}</span>
                {i.version_number ? (
                  <span className="ml-1 text-slate-500 dark:text-slate-400">{i.version_number}</span>
                ) : null}
                {i.reason === 'dependency' ? (
                  <span className="ml-1 text-xs text-slate-500 dark:text-slate-400">
                    · {i.required_by.join('、')} 的依赖
                  </span>
                ) : null}
              </div>
              <Badge tone={st.tone}>{st.label}</Badge>
            </li>
          );
        })}
      </ul>
      {plan.warnings.map((w) => (
        <p key={w} className="text-xs text-yellow-700 dark:text-yellow-400">
          {w}
        </p>
      ))}
      {plan.install_count > 0 ? (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          共下载约 {formatBytes(plan.download_bytes)}，每个文件都会校验
          sha512；任一失败则全部撤回。装好后需重启服务器生效。
        </p>
      ) : null}
    </div>
  );
}
