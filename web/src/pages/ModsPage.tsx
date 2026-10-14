import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/http';
import {
  modsApi,
  type HealthIssue,
  type ModInfo,
  type ModsOverview,
  type ModUpdate,
} from '@/api/mods';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Input } from '@/components/ui/Input';
import { PageHeader } from '@/components/PageHeader';
import { Tabs, type TabDef } from '@/components/Tabs';
import { useUrlTab } from '@/lib/useUrlTab';
import { formatDateTime } from '@/lib/format';
import { formatBytes } from '@/lib/ops';

import { ConfigFilesTab } from './mods/ConfigFilesTab';
import { GameVersionTab } from './mods/GameVersionTab';
import { ImportTab } from './mods/ImportTab';
import { InstallTab } from './mods/InstallTab';
import { scrollBox } from '@/lib/scroll';

function errText(e: unknown): string {
  return e instanceof ApiError ? e.detail : '操作失败';
}

// ---- 版本卡 ----

function VersionCard({ data }: { data: ModsOverview }) {
  const loaderOutdated =
    data.loader_latest !== null && data.loader_version !== null && data.loader_latest !== data.loader_version;
  return (
    <Card>
      <CardHeader title="版本信息" />
      <CardBody>
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-slate-500 dark:text-slate-400">Minecraft</dt>
            <dd className="font-medium">{data.mc_version ?? '未知'}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">Fabric Loader（当前）</dt>
            <dd className="font-medium">{data.loader_version ?? '未知'}</dd>
          </div>
          <div>
            <dt className="text-slate-500 dark:text-slate-400">Fabric Loader（最新稳定）</dt>
            <dd className="font-medium">
              {data.loader_latest ?? '—'}
              {loaderOutdated ? <Badge tone="yellow">可升级</Badge> : null}
            </dd>
          </div>
        </dl>
        <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
          MC 本体与 Loader 升级暂需手动操作，此页只做展示与提示。
        </p>
      </CardBody>
    </Card>
  );
}

// ---- 待重启横幅 ----

function PendingRestartBanner({ data }: { data: ModsOverview }) {
  const pending = data.pending_restart;
  if (!pending) return null;
  return (
    <div
      role="alert"
      className="rounded-md border border-yellow-300 bg-yellow-50 px-4 py-3 text-sm dark:border-yellow-800 dark:bg-yellow-900/20"
    >
      <div className="font-medium text-yellow-800 dark:text-yellow-300">
        有改动需要重启服务器才能生效
      </div>
      <ul className="mt-1 list-inside list-disc text-yellow-700 dark:text-yellow-400">
        {pending.changes.map((c, i) => (
          <li key={i}>
            {describeChange(c)}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-yellow-700 dark:text-yellow-400">
        自 {formatDateTime(pending.since)} 起 ·{' '}
        <Link to="/" className="underline">
          前往总览页重启
        </Link>
      </p>
    </div>
  );
}

function describeChange(c: { action: string; [k: string]: unknown }): string {
  const file = typeof c.file === 'string' ? c.file : '';
  switch (c.action) {
    case 'disable':
      return `停用 ${file}`;
    case 'enable':
      return `启用 ${file}`;
    case 'update':
      return `更新 ${file} → ${typeof c.to === 'string' ? c.to : ''}`;
    case 'install':
      return `安装 ${file}`;
    case 'upload':
      return `上传 ${file}`;
    case 'mrpack':
      return `导入整合包 ${file}`;
    case 'config':
      return `修改配置 ${file}`;
    case 'rollback':
      return `回退 ${typeof c.removed === 'string' ? c.removed : ''} → ${typeof c.restored === 'string' ? c.restored : ''}`;
    default:
      return c.action;
  }
}

// ---- 健康问题卡 ----

function IssuesCard({ issues }: { issues: HealthIssue[] }) {
  if (issues.length === 0) {
    return (
      <Card>
        <CardHeader title="健康检查" />
        <CardBody>
          <p className="text-sm text-green-600 dark:text-green-400">未发现问题。</p>
        </CardBody>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader title={`健康检查（${issues.length}）`} />
      <CardBody className="space-y-2">
        {issues.map((it, i) => (
          <div
            key={i}
            className={
              'rounded-md border px-3 py-2 text-sm ' +
              (it.level === 'error'
                ? 'border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-300'
                : 'border-yellow-300 bg-yellow-50 text-yellow-800 dark:border-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-300')
            }
          >
            <Badge tone={it.level === 'error' ? 'red' : 'yellow'}>
              {it.level === 'error' ? '错误' : '警告'}
            </Badge>
            <span className="ml-2 font-mono text-xs">{it.mod}</span>
            <span className="ml-2">{it.message}</span>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

// ---- 更新徽章 ----

function UpdateBadge({ update }: { update: ModUpdate | undefined }) {
  if (!update || update.source === 'unknown') {
    return <Badge tone="gray">非 Modrinth</Badge>;
  }
  if (update.has_update) {
    return <Badge tone="blue">可更新 → {update.version_number}</Badge>;
  }
  return <Badge tone="green">已是最新</Badge>;
}

// ---- Mod 行 ----

interface RowActions {
  observe: boolean;
  busy: boolean;
  onToggle: (mod: ModInfo) => void;
  onUpdate: (mod: ModInfo) => void;
  onRollback: (mod: ModInfo) => void;
}

function ModRow({
  mod,
  update,
  actions,
}: {
  mod: ModInfo;
  update: ModUpdate | undefined;
  actions: RowActions;
}) {
  const { observe, busy } = actions;
  const canUpdate = Boolean(update && update.source === 'modrinth' && update.has_update);
  return (
    <tr className="border-b border-slate-100 last:border-0 dark:border-slate-800">
      <td className="px-3 py-2">
        <div className="font-medium">{mod.name}</div>
        <div className="text-xs text-slate-400">
          {mod.id || '（无 id）'}
          {mod.kind === 'unknown' ? <span className="ml-1 text-red-500">· 非 Fabric / 损坏</span> : null}
        </div>
      </td>
      <td className="hidden px-3 py-2 whitespace-nowrap sm:table-cell">{mod.version || '—'}</td>
      <td className="hidden px-3 py-2 lg:table-cell">
        {mod.homepage ? (
          <a href={mod.homepage} target="_blank" rel="noreferrer" className="font-mono text-xs underline">
            {mod.file}
          </a>
        ) : (
          <span className="font-mono text-xs">{mod.file}</span>
        )}
      </td>
      <td className="hidden px-3 py-2 whitespace-nowrap text-right lg:table-cell">{formatBytes(mod.size)}</td>
      <td className="px-3 py-2">
        <UpdateBadge update={update} />
      </td>
      <td className="hidden px-3 py-2 whitespace-nowrap sm:table-cell">
        <label className="inline-flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            className="h-4 w-4 accent-brand"
            checked={mod.enabled}
            disabled={observe || busy}
            onChange={() => actions.onToggle(mod)}
            aria-label={mod.enabled ? `停用 ${mod.file}` : `启用 ${mod.file}`}
          />
          <span className="text-xs text-slate-500">{mod.enabled ? '已启用' : '已停用'}</span>
        </label>
      </td>
      <td className="px-3 py-2 whitespace-nowrap text-right">
        <div className="flex justify-end gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={observe || busy || !canUpdate}
            onClick={() => actions.onUpdate(mod)}
          >
            更新
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={observe || busy}
            onClick={() => actions.onRollback(mod)}
          >
            回退
          </Button>
        </div>
      </td>
    </tr>
  );
}

function ModTable({
  title,
  mods,
  updates,
  actions,
}: {
  title: string;
  mods: ModInfo[];
  updates: Record<string, ModUpdate>;
  actions: RowActions;
}) {
  return (
    <Card>
      <CardHeader title={`${title}（${mods.length}）`} />
      <CardBody className="p-0">
        <div className={scrollBox('lg', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="已安装 Mod">
          <table className="w-full text-sm" data-responsive>
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800">
                <th className="px-3 py-2 font-medium">名称</th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">版本</th>
                <th className="hidden px-3 py-2 font-medium lg:table-cell">文件名</th>
                <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">大小</th>
                <th className="px-3 py-2 font-medium">来源 / 更新</th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">启用</th>
                <th className="px-3 py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {mods.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-slate-500 dark:text-slate-400">
                    没有匹配的 mod
                  </td>
                </tr>
              ) : (
                mods.map((mod) => (
                  <ModRow key={mod.file} mod={mod} update={updates[mod.file]} actions={actions} />
                ))
              )}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

// ---- 页面 ----

function InstalledMods() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState('');
  const [confirmUpdateAll, setConfirmUpdateAll] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['mods'],
    queryFn: ({ signal }) => modsApi.overview(signal),
    retry: false,
  });

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['mods'] });
  const onErr = (e: unknown) => setActionError(errText(e));

  const checkMut = useMutation({
    mutationFn: () => modsApi.checkUpdates(),
    onSuccess: (data) => {
      qc.setQueryData(['mods'], data);
      setNotice('更新检查完成' + (data.update_error ? `（${data.update_error}）` : ''));
    },
    onError: onErr,
  });

  const toggleMut = useMutation({
    mutationFn: (mod: ModInfo) => (mod.enabled ? modsApi.disable(mod.file) : modsApi.enable(mod.file)),
    onSuccess: invalidate,
    onError: onErr,
  });

  const updateMut = useMutation({
    mutationFn: (mod: ModInfo) => modsApi.update(mod.file),
    onSuccess: (r) => {
      invalidate();
      setNotice(`已更新 ${r.file} → ${r.new_file ?? ''}（待重启生效）`);
    },
    onError: onErr,
  });

  const rollbackMut = useMutation({
    mutationFn: (mod: ModInfo) => modsApi.rollback(mod.file),
    onSuccess: (r) => {
      invalidate();
      setNotice(`已回退：恢复 ${r.restored}`);
    },
    onError: onErr,
  });

  const updateAllMut = useMutation({
    mutationFn: () => modsApi.updateAll(),
    onSuccess: (r) => {
      invalidate();
      const ok = r.results.filter((x) => x.ok).length;
      const failed = r.results.length - ok;
      setNotice(`批量更新完成：成功 ${ok}，失败 ${failed}`);
    },
    onError: onErr,
  });

  const observe = query.error instanceof ApiError && query.error.status === 409;

  const data = query.data;

  const { enabled, disabled, updatableCount } = useMemo(() => {
    const all = data?.mods ?? [];
    const f = filter.trim().toLowerCase();
    const match = (m: ModInfo) =>
      !f ||
      m.name.toLowerCase().includes(f) ||
      m.id.toLowerCase().includes(f) ||
      m.file.toLowerCase().includes(f);
    const filtered = all.filter(match);
    const ups = data?.updates ?? {};
    const count = all.filter((m) => {
      const u = ups[m.file];
      return m.enabled && u && u.source === 'modrinth' && u.has_update;
    }).length;
    return {
      enabled: filtered.filter((m) => m.enabled),
      disabled: filtered.filter((m) => !m.enabled),
      updatableCount: count,
    };
  }, [data, filter]);

  if (observe) {
    return (
      <div className="space-y-4">
        <Card>
          <CardBody>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              观测模式：mod 的启停与升级暂由手动操作，面板不接管。切换到控制模式后此页可用。
            </p>
          </CardBody>
        </Card>
      </div>
    );
  }

  const busy =
    toggleMut.isPending || updateMut.isPending || rollbackMut.isPending || updateAllMut.isPending;

  const actions: RowActions = {
    observe: false,
    busy,
    onToggle: (mod) => {
      setActionError(null);
      toggleMut.mutate(mod);
    },
    onUpdate: (mod) => {
      setActionError(null);
      updateMut.mutate(mod);
    },
    onRollback: (mod) => {
      setActionError(null);
      rollbackMut.mutate(mod);
    },
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {data?.checked_at ? (
          <span className="text-xs text-slate-500 dark:text-slate-400">
            上次检查：{formatDateTime(data.checked_at)}
          </span>
        ) : (
          <span className="text-xs text-slate-500 dark:text-slate-400">尚未检查更新</span>
        )}
        <Button
          size="sm"
          variant="secondary"
          disabled={checkMut.isPending}
          onClick={() => {
            setActionError(null);
            setNotice(null);
            checkMut.mutate();
          }}
        >
          {checkMut.isPending ? '检查中…' : '检查更新'}
        </Button>
        <Button
          size="sm"
          disabled={busy || updatableCount === 0}
          onClick={() => {
            setActionError(null);
            setConfirmUpdateAll(true);
          }}
        >
          全部更新{updatableCount > 0 ? `（${updatableCount}）` : ''}
        </Button>
      </div>

      {query.isError && !observe ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          无法获取 mod 列表，正在重试…
        </p>
      ) : null}
      {actionError ? (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          {notice}
        </p>
      ) : null}
      {data?.update_error ? (
        <p className="text-sm text-yellow-700 dark:text-yellow-400">更新检查提示：{data.update_error}</p>
      ) : null}

      {data ? <PendingRestartBanner data={data} /> : null}
      {data ? <VersionCard data={data} /> : null}
      {data ? <IssuesCard issues={data.issues} /> : null}

      <div className="max-w-xs">
        <Input
          placeholder="按名称 / id / 文件名过滤"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="过滤 mod"
        />
      </div>

      {query.isLoading ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>
      ) : (
        <>
          <ModTable
            title="已启用"
            mods={enabled}
            updates={data?.updates ?? {}}
            actions={actions}
          />
          <ModTable
            title="已停用"
            mods={disabled}
            updates={data?.updates ?? {}}
            actions={actions}
          />
        </>
      )}

      <ConfirmDialog
        open={confirmUpdateAll}
        title="更新全部可升级的 mod？"
        description={`将逐个下载并替换 ${updatableCount} 个 mod，旧版本会保留以便回退。改动需要重启服务器才能生效。`}
        confirmLabel="全部更新"
        danger={false}
        onConfirm={() => {
          setConfirmUpdateAll(false);
          updateAllMut.mutate();
        }}
        onCancel={() => setConfirmUpdateAll(false)}
      />
    </div>
  );
}

const MOD_TABS: TabDef[] = [
  { key: 'installed', label: '已安装' },
  { key: 'browse', label: '安装 Mod' },
  { key: 'import', label: '上传与整合包' },
  { key: 'config', label: '配置文件' },
  { key: 'game', label: '游戏版本' },
];

const MOD_SUBTITLE: Record<string, string> = {
  installed: '已装 mod、Modrinth 更新检查、启停与回退',
  browse: '从 Modrinth 搜索并一键安装，自动处理必需依赖',
  import: '上传 Modrinth 上没有的 jar，或导入 .mrpack 整合包',
  config: '在线编辑 config/ 下的 mod 配置，保存前校验语法并备份',
  game: '一键升级 Minecraft 与 Fabric Loader，自动处理 Java 和 mod 兼容，失败自动回滚',
};

export function ModsPage() {
  const [active, select] = useUrlTab(MOD_TABS, 'installed');
  return (
    <div className="space-y-4">
      <PageHeader title="Mod 与升级" subtitle={MOD_SUBTITLE[active]} />
      <Tabs tabs={MOD_TABS} active={active} onSelect={select} label="Mod 分区" />
      <div role="tabpanel">
        {active === 'installed' ? <InstalledMods /> : null}
        {active === 'browse' ? <InstallTab /> : null}
        {active === 'import' ? <ImportTab /> : null}
        {active === 'config' ? <ConfigFilesTab /> : null}
        {active === 'game' ? <GameVersionTab /> : null}
      </div>
    </div>
  );
}
