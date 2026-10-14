import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { gameApi } from '@/api/game';
import { ApiError } from '@/api/http';
import { systemApi, type SetupRequest } from '@/api/system';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';

const sel =
  'h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100';

function err(e: unknown): string {
  return e instanceof ApiError ? e.detail : e instanceof Error ? e.message : '操作失败';
}

/** 游戏目录里还没有服务端时显示：选版本、填几项基本设置、同意 EULA，一键装好并开服。 */
export function SetupWizard() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ['setupStatus'],
    queryFn: () => systemApi.setupStatus(),
    refetchInterval: (q) => (q.state.data?.running_job ? 3000 : false),
  });
  const versions = useQuery({
    queryKey: ['game', 'versions', false],
    queryFn: () => gameApi.versions(false),
    enabled: !!status.data?.needs_setup,
  });
  const total = status.data?.memory_total_gb ?? 8;
  const [form, setForm] = useState<SetupRequest>({
    mc: '',
    loader: '',
    accept_eula: false,
    memory_gb: 4,
    motd: 'A Minecraft Server',
    max_players: 20,
    online_mode: false,
    white_list: true,
    difficulty: 'normal',
    view_distance: 10,
    port: 25565,
    java_mirror: 'tuna',
  });
  const set = <K extends keyof SetupRequest>(k: K, v: SetupRequest[K]) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    const v = versions.data;
    if (v && !form.mc)
      setForm((f) => ({ ...f, mc: v.games[0]?.version ?? '', loader: v.loaders[0]?.version ?? '' }));
  }, [versions.data, form.mc]);
  useEffect(() => {
    // 默认给服务端一半内存，至少 2 GB，最多 total-2
    if (status.data) set('memory_gb', Math.max(2, Math.min(Math.floor(total / 2), total - 2)));
  }, [status.data?.memory_total_gb]); // eslint-disable-line react-hooks/exhaustive-deps

  const install = useMutation({
    mutationFn: () => systemApi.setupInstall(form),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['setupStatus'] }),
  });

  useEffect(() => {
    if (status.data && !status.data.needs_setup) {
      void qc.invalidateQueries({ queryKey: ['serverStatus'] });
    }
  }, [status.data, qc]);

  if (!status.data?.needs_setup) return null;
  const running = status.data.running_job === 'setup_install';

  return (
    <Card>
      <CardHeader title="安装 Minecraft 服务端" />
      <CardBody className="space-y-4 text-sm">
        <p className="text-slate-600 dark:text-slate-300">
          游戏目录 <code className="font-mono text-xs">{status.data.game_dir}</code>{' '}
          里还没有服务端。选好版本和基本设置，mc-ops 会下载 Fabric 服务端和所需的
          Java、试启动验证，然后生成世界并开服。之后所有设置都可以在面板里改。
        </p>
        {running ? (
          <p className="text-slate-700 dark:text-slate-200">
            正在安装…（下载和首次生成世界通常要几分钟）。实时进度见
            <Link to="/ops" className="mx-1 underline">
              运维任务
            </Link>
            。
          </p>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">Minecraft 版本</span>
                <select className={sel} value={form.mc} onChange={(e) => set('mc', e.target.value)}>
                  {(versions.data?.games ?? []).map((g) => (
                    <option key={g.version}>{g.version}</option>
                  ))}
                </select>
              </label>
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">Fabric Loader</span>
                <select className={sel} value={form.loader} onChange={(e) => set('loader', e.target.value)}>
                  {(versions.data?.loaders ?? []).map((l) => (
                    <option key={l.version}>{l.version}</option>
                  ))}
                </select>
              </label>
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">
                  内存：{form.memory_gb} GB（本机共 {total} GB）
                </span>
                <input
                  type="range"
                  className="w-full"
                  min={1}
                  max={Math.max(1, total - 1)}
                  value={form.memory_gb}
                  onChange={(e) => set('memory_gb', Number(e.target.value))}
                />
              </label>
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">服务器名称（MOTD）</span>
                <Input value={form.motd} maxLength={120} onChange={(e) => set('motd', e.target.value)} />
              </label>
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">最大玩家数</span>
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  value={form.max_players}
                  onChange={(e) => set('max_players', Number(e.target.value))}
                />
              </label>
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">难度</span>
                <select
                  className={sel}
                  value={form.difficulty}
                  onChange={(e) => set('difficulty', e.target.value as SetupRequest['difficulty'])}
                >
                  <option value="peaceful">和平</option>
                  <option value="easy">简单</option>
                  <option value="normal">普通</option>
                  <option value="hard">困难</option>
                </select>
              </label>
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">游戏端口</span>
                <Input
                  type="number"
                  min={1}
                  max={65535}
                  value={form.port}
                  onChange={(e) => set('port', Number(e.target.value))}
                />
              </label>
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">视距（区块）</span>
                <Input
                  type="number"
                  min={3}
                  max={32}
                  value={form.view_distance}
                  onChange={(e) => set('view_distance', Number(e.target.value))}
                />
              </label>
              <label>
                <span className="mb-1 block text-slate-600 dark:text-slate-300">Java 下载源（需要时）</span>
                <select
                  className={sel}
                  value={form.java_mirror}
                  onChange={(e) => set('java_mirror', e.target.value as 'tuna' | 'official')}
                >
                  <option value="tuna">清华镜像（国内快）</option>
                  <option value="official">官方 GitHub</option>
                </select>
              </label>
            </div>
            <div className="space-y-2">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={form.white_list}
                  onChange={(e) => set('white_list', e.target.checked)}
                />
                <span>开启白名单（推荐：只有加进名单的玩家能进服）</span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={form.online_mode}
                  onChange={(e) => set('online_mode', e.target.checked)}
                />
                <span>
                  正版验证（online-mode）。不勾选即离线模式：玩家不需要正版账号，但任何人都能冒用别人的名字，务必配合白名单并安装
                  EasyAuth。
                </span>
              </label>
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={form.accept_eula}
                  onChange={(e) => set('accept_eula', e.target.checked)}
                />
                <span>
                  我已阅读并同意{' '}
                  <a
                    href="https://aka.ms/MinecraftEULA"
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-brand underline"
                  >
                    Minecraft 最终用户许可协议（EULA）
                  </a>
                  （开服必需）
                </span>
              </label>
            </div>
            <div className="flex items-center gap-3">
              <Button
                disabled={!form.accept_eula || !form.mc || !form.loader || install.isPending}
                onClick={() => install.mutate()}
              >
                {install.isPending ? '提交中…' : '安装并开服'}
              </Button>
              {!form.accept_eula ? <span className="text-xs text-slate-500">需要先同意 EULA</span> : null}
            </div>
            {install.isError ? <p className="text-red-600 dark:text-red-400">{err(install.error)}</p> : null}
            {versions.isError ? (
              <p className="text-red-600 dark:text-red-400">读取版本列表失败：{err(versions.error)}</p>
            ) : null}
          </>
        )}
      </CardBody>
    </Card>
  );
}
