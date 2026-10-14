import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { gameApi, type ModCompat, type Precheck } from '@/api/game';
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { formatDateTime } from '@/lib/format';

import { errText } from './util';
import { scrollBox } from '@/lib/scroll';

const COMPAT: Record<ModCompat, { label: string; tone: BadgeTone }> = {
  update: { label: '将更新', tone: 'blue' },
  compatible: { label: '无需更新', tone: 'green' },
  unknown: { label: '找不到兼容版本', tone: 'yellow' },
};

const selectCls =
  'h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100';

export function GameVersionTab() {
  const qc = useQueryClient();
  const [snapshots, setSnapshots] = useState(false);
  const [mc, setMc] = useState('');
  const [loader, setLoader] = useState('');
  const [disableUnknown, setDisableUnknown] = useState(false);
  const [mirror, setMirror] = useState<'tuna' | 'official'>('tuna');
  const [preparing, setPreparing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [started, setStarted] = useState(false);

  const versions = useQuery({
    queryKey: ['game', 'versions', snapshots],
    queryFn: () => gameApi.versions(snapshots),
    refetchInterval: preparing ? 3000 : false,
  });
  const v = versions.data;

  useEffect(() => {
    if (v && !mc) setMc(v.current.mc ?? v.games[0]?.version ?? '');
    if (v && !loader) setLoader(v.loaders[0]?.version ?? '');
  }, [v, mc, loader]);

  // 准备在后台跑：结果出现（或报错）就停止轮询
  useEffect(() => {
    if (preparing && v && (v.prepared || v.prepare_error)) setPreparing(false);
  }, [preparing, v]);

  const target = { mc, loader, installer: v?.installer ?? null };
  const same = v?.current.mc === mc && v?.current.loader === loader;

  const check = useMutation({ mutationFn: () => gameApi.precheck(target) });
  const plan = check.data;
  useEffect(() => check.reset(), [mc, loader]); // eslint-disable-line react-hooks/exhaustive-deps

  const prepare = useMutation({
    mutationFn: () => gameApi.prepare({ ...target, disable_unknown: disableUnknown, java_mirror: mirror }),
    onSuccess: () => {
      setPreparing(true);
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
  const upgrade = useMutation({
    mutationFn: () => gameApi.upgrade(target),
    onSuccess: () => {
      setStarted(true);
      void qc.invalidateQueries({ queryKey: ['game'] });
      void qc.invalidateQueries({ queryKey: ['jobs'] });
    },
  });

  const prepared =
    v?.prepared && v.prepared.target.mc === mc && v.prepared.target.loader === loader ? v.prepared : null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="当前版本" />
        <CardBody className="grid gap-3 text-sm sm:grid-cols-3">
          <Info label="Minecraft" value={v?.current.mc ?? '—'} />
          <Info label="Fabric Loader" value={v?.current.loader ?? '—'} />
          <Info
            label="Java"
            value={v ? `${v.current.java_major ?? '未检测到'}（${v.current.java_bin}）` : '—'}
          />
          {v && !v.unit_managed ? (
            <p className="text-yellow-700 dark:text-yellow-400 sm:col-span-3">
              服务端的 systemd 单元不是 mc-ops 生成的，升级前需要先在
              <Link to="/settings?tab=config" className="mx-1 underline">
                设置 → 服务器配置
              </Link>
              的「服务端 → 高级」里打开「由 mc-ops 生成 systemd 单元」。
            </p>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="选择目标版本" />
        <CardBody className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block text-slate-600 dark:text-slate-300">Minecraft</span>
              <select className={selectCls} value={mc} onChange={(e) => setMc(e.target.value)}>
                {(v?.games ?? []).map((g) => (
                  <option key={g.version} value={g.version}>
                    {g.version}
                    {g.version === v?.current.mc ? '（当前）' : ''}
                    {!g.stable ? '（快照）' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-slate-600 dark:text-slate-300">Fabric Loader</span>
              <select className={selectCls} value={loader} onChange={(e) => setLoader(e.target.value)}>
                {(v?.loaders ?? []).map((l) => (
                  <option key={l.version} value={l.version}>
                    {l.version}
                    {l.version === v?.current.loader ? '（当前）' : ''}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label className="inline-flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
              <input type="checkbox" checked={snapshots} onChange={(e) => setSnapshots(e.target.checked)} />
              显示快照版本（不建议用于正式服）
            </label>
            <Button disabled={!mc || !loader || same || check.isPending} onClick={() => check.mutate()}>
              {check.isPending ? '检查中…' : same ? '已是当前版本' : '检查兼容性'}
            </Button>
          </div>
          {versions.isError ? (
            <p className="text-sm text-red-600 dark:text-red-400">{errText(versions.error)}</p>
          ) : null}
          {check.isError ? (
            <p className="text-sm text-red-600 dark:text-red-400">{errText(check.error)}</p>
          ) : null}
        </CardBody>
      </Card>

      {plan ? (
        <PlanCard
          plan={plan}
          disableUnknown={disableUnknown}
          setDisableUnknown={setDisableUnknown}
          mirror={mirror}
          setMirror={setMirror}
        />
      ) : null}

      {plan && plan.blockers.length === 0 ? (
        <Card>
          <CardHeader title="执行" />
          <CardBody className="space-y-3 text-sm">
            <ol className="list-inside list-decimal space-y-1 text-slate-600 dark:text-slate-300">
              <li>
                <b>准备</b>（不停服）：下载新版本、兼容的 mod 和所需
                Java，并在暂存目录里试启动一次，确认新版本能起来、依赖已下全。
              </li>
              <li>
                <b>升级</b>（停服几分钟）：游戏内倒计时 → 停服 → 完整冷备份 → 替换文件 →
                开服。新版本起不来会自动用冷备份恢复世界并回到旧版本。
              </li>
            </ol>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                disabled={preparing || prepare.isPending}
                onClick={() => prepare.mutate()}
              >
                {preparing || prepare.isPending ? '准备中…' : prepared ? '重新准备' : '1. 准备'}
              </Button>
              <Button
                variant="danger"
                disabled={!prepared || upgrade.isPending || started}
                onClick={() => setConfirm(true)}
              >
                2. 升级到 {mc}
              </Button>
              <Link to="/ops" className="text-xs text-brand underline">
                在「运维任务」查看实时日志
              </Link>
            </div>
            {prepare.isError ? (
              <p className="text-red-600 dark:text-red-400">{errText(prepare.error)}</p>
            ) : null}
            {v?.prepare_error && !preparing ? (
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-red-50 p-2 text-xs text-red-700 dark:bg-red-950/40 dark:text-red-300">
                准备失败：{v.prepare_error}
              </pre>
            ) : null}
            {prepared ? (
              <p className="text-green-700 dark:text-green-400">
                已准备好（{formatDateTime(prepared.prepared_at)}）：试启动通过，将更新{' '}
                {prepared.mod_updates.length} 个 mod
                {prepared.disable.length ? `、停用 ${prepared.disable.length} 个` : ''}。24 小时内有效。
              </p>
            ) : null}
            {upgrade.isError ? (
              <p className="text-red-600 dark:text-red-400">{errText(upgrade.error)}</p>
            ) : null}
            {started ? (
              <p className="text-slate-700 dark:text-slate-200">
                升级已开始，玩家会先收到倒计时提示。进度见
                <Link to="/ops" className="mx-1 underline">
                  运维任务
                </Link>
                。
              </p>
            ) : null}
          </CardBody>
        </Card>
      ) : null}

      <ConfirmDialog
        open={confirm}
        title={`升级到 Minecraft ${mc}？`}
        description="在线玩家会收到倒计时提示，随后停服几分钟：先做完整冷备份，再替换服务端和 mod 并开服。新版本第一次加载会转换世界存档；如果开不起来，会自动从备份恢复并回到旧版本。"
        confirmLabel="开始升级"
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          upgrade.mutate();
        }}
      />
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-0.5 break-all font-medium">{value}</div>
    </div>
  );
}

function PlanCard({
  plan,
  disableUnknown,
  setDisableUnknown,
  mirror,
  setMirror,
}: {
  plan: Precheck;
  disableUnknown: boolean;
  setDisableUnknown: (b: boolean) => void;
  mirror: 'tuna' | 'official';
  setMirror: (m: 'tuna' | 'official') => void;
}) {
  const unknown = plan.mods.filter((m) => m.status === 'unknown').length;
  return (
    <Card>
      <CardHeader title={`兼容性检查：${plan.current.mc ?? '无'} → ${plan.target.mc}`} />
      <CardBody className="space-y-3 text-sm">
        {plan.blockers.map((b) => (
          <p
            key={b}
            role="alert"
            className="rounded bg-red-50 p-2 text-red-700 dark:bg-red-950/40 dark:text-red-300"
          >
            无法升级：{b}
          </p>
        ))}
        {plan.warnings.map((w) => (
          <p key={w} className="text-yellow-700 dark:text-yellow-400">
            {w}
          </p>
        ))}
        <div className="flex flex-wrap items-center gap-2">
          <span>
            Java：需要 {plan.java.required}，当前 {plan.java.current ?? '未检测到'}
          </span>
          {plan.java.needs_install ? (
            <>
              <Badge tone="blue">将自动安装 Temurin {plan.java.required}</Badge>
              <label className="inline-flex items-center gap-1 text-xs">
                下载源
                <select
                  className="h-7 rounded border border-slate-300 bg-white px-1 text-xs dark:border-slate-700 dark:bg-slate-900"
                  value={mirror}
                  onChange={(e) => setMirror(e.target.value as 'tuna' | 'official')}
                >
                  <option value="tuna">清华镜像（国内快）</option>
                  <option value="official">官方 GitHub</option>
                </select>
              </label>
            </>
          ) : (
            <Badge tone="green">满足</Badge>
          )}
        </div>
        <div className={scrollBox('md', 'overflow-x-auto')} tabIndex={0} role="region" aria-label="Mod 兼容性">
          <table className="w-full text-left text-sm" data-responsive>
            <thead className="text-xs text-slate-500 dark:text-slate-400">
              <tr>
                <th className="py-1.5 pr-3 font-medium">Mod</th>
                <th className="hidden py-1.5 pr-3 font-medium sm:table-cell">当前</th>
                <th className="py-1.5 pr-3 font-medium">目标版本</th>
                <th className="py-1.5 font-medium">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {plan.mods.map((m) => (
                <tr key={m.file}>
                  <td className="py-1.5 pr-3">{m.name}</td>
                  <td className="hidden py-1.5 pr-3 text-slate-500 sm:table-cell">{m.version}</td>
                  <td className="py-1.5 pr-3 text-slate-500">
                    {m.new_version ?? (m.status === 'compatible' ? m.version : '—')}
                  </td>
                  <td className="py-1.5">
                    <Badge tone={COMPAT[m.status].tone}>{COMPAT[m.status].label}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {unknown > 0 ? (
          <label className="inline-flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={disableUnknown}
              onChange={(e) => setDisableUnknown(e.target.checked)}
            />
            <span>
              升级时停用这 {unknown} 个找不到兼容版本的
              mod（移到备份目录，可以手动恢复）。不勾选的话它们会保留，可能导致新版本起不来——那样会自动回滚。
            </span>
          </label>
        ) : null}
      </CardBody>
    </Card>
  );
}
