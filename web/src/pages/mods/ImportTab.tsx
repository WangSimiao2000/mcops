import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';

import { modStoreApi, type MrpackPlan } from '@/api/modstore';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { formatBytes } from '@/lib/ops';

import { errText } from './util';

function FilePick({
  accept,
  label,
  busy,
  onFile,
}: {
  accept: string;
  label: string;
  busy: boolean;
  onFile: (f: File) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        const f = e.dataTransfer.files[0];
        if (f) onFile(f);
      }}
      className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center text-sm transition-colors ${
        drag
          ? 'border-brand bg-blue-50 dark:bg-blue-950/30'
          : 'border-slate-300 text-slate-500 dark:border-slate-700 dark:text-slate-400'
      }`}
    >
      <p>把文件拖到这里，或</p>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => ref.current?.click()}>
        {busy ? '处理中…' : label}
      </Button>
      <input
        ref={ref}
        type="file"
        accept={accept}
        className="hidden"
        aria-label={label}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = '';
        }}
      />
    </div>
  );
}

export function ImportTab() {
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: ['mods'] });

  const [uploadMsg, setUploadMsg] = useState<{
    ok: boolean;
    text: string;
    warnings?: string[];
  } | null>(null);
  const upload = useMutation({
    mutationFn: (f: File) => modStoreApi.upload(f),
    onSuccess: (r) => {
      refresh();
      setUploadMsg({
        ok: true,
        text: `已安装 ${r.name} ${r.version}（${r.file}），重启服务器后生效`,
        warnings: r.warnings,
      });
    },
    onError: (e) => setUploadMsg({ ok: false, text: errText(e) }),
  });

  const [pack, setPack] = useState<MrpackPlan | null>(null);
  const [packMsg, setPackMsg] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const plan = useMutation({
    mutationFn: (f: File) => modStoreApi.mrpackPlan(f),
    onSuccess: (p) => {
      setPack(p);
      setPackMsg(null);
    },
    onError: (e) => setPackMsg(errText(e)),
  });
  const apply = useMutation({
    mutationFn: (token: string) => modStoreApi.mrpackApply(token),
    onSuccess: (r) => {
      refresh();
      setPack(null);
      setPackMsg(
        `已写入 ${r.written.length} 个文件` +
          (r.replaced.length
            ? `，替换 ${r.replaced.length} 个（旧文件已备份${r.backup_dir ? `到 ${r.backup_dir}` : '到 mods/disabled/'}）`
            : '') +
          '。重启服务器后生效。',
      );
    },
    onError: (e) => {
      setPack(null);
      setPackMsg(errText(e));
    },
  });

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="上传 mod（.jar）" />
        <CardBody className="space-y-3">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            用于 Modrinth 上没有的 mod。会检查是不是 Fabric mod、是否和已装的重复，并提示版本不匹配。
          </p>
          <FilePick
            accept=".jar"
            label="选择 jar 文件"
            busy={upload.isPending}
            onFile={(f) => upload.mutate(f)}
          />
          {uploadMsg ? (
            <div
              className={`text-sm ${uploadMsg.ok ? 'text-green-700 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}
            >
              <p>{uploadMsg.text}</p>
              {uploadMsg.warnings?.map((w) => (
                <p key={w} className="text-yellow-700 dark:text-yellow-400">
                  {w}
                </p>
              ))}
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="导入整合包（.mrpack）" />
        <CardBody className="space-y-3">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Modrinth 整合包格式。只会写入 mods/ 和 config/ 等目录，跳过纯客户端文件，不碰世界存档、白名单和
            server.properties；被覆盖的文件会先备份。
          </p>
          {!pack ? (
            <FilePick
              accept=".mrpack,.zip"
              label="选择 .mrpack 文件"
              busy={plan.isPending}
              onFile={(f) => plan.mutate(f)}
            />
          ) : (
            <MrpackPreview
              plan={pack}
              busy={apply.isPending}
              onCancel={() => setPack(null)}
              onApply={() => setConfirm(true)}
            />
          )}
          {packMsg ? <p className="text-sm text-slate-700 dark:text-slate-200">{packMsg}</p> : null}
        </CardBody>
      </Card>

      <ConfirmDialog
        open={confirm && pack !== null}
        title={`导入整合包「${pack?.name ?? ''}」？`}
        description={`将下载 ${pack?.files.length ?? 0} 个文件（约 ${formatBytes(pack?.download_bytes ?? 0)}）并写入 ${pack?.overrides.length ?? 0} 个配置文件，同名文件会先备份。`}
        confirmLabel="导入"
        danger={false}
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          if (pack) apply.mutate(pack.token);
        }}
      />
    </div>
  );
}

function MrpackPreview({
  plan,
  busy,
  onCancel,
  onApply,
}: {
  plan: MrpackPlan;
  busy: boolean;
  onCancel: () => void;
  onApply: () => void;
}) {
  return (
    <div className="space-y-3 text-sm">
      <div>
        <p className="font-medium">
          {plan.name} <span className="text-slate-500 dark:text-slate-400">{plan.version}</span>
        </p>
        {plan.summary ? <p className="text-slate-600 dark:text-slate-300">{plan.summary}</p> : null}
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          {Object.entries(plan.dependencies)
            .map(([k, v]) => `${k} ${v}`)
            .join(' · ')}
        </p>
      </div>
      {plan.warnings.map((w) => (
        <p key={w} className="text-yellow-700 dark:text-yellow-400">
          {w}
        </p>
      ))}
      <details open>
        <summary className="cursor-pointer select-none">
          下载 {plan.files.length} 个文件（约 {formatBytes(plan.download_bytes)}
          ）· 覆盖 {plan.overrides.length} 个配置
        </summary>
        <ul className="mt-2 max-h-48 space-y-0.5 overflow-y-auto font-mono text-xs text-slate-600 dark:text-slate-300">
          {plan.files.map((f) => (
            <li key={f.path}>{f.path}</li>
          ))}
          {plan.overrides.map((o) => (
            <li key={o}>{o}</li>
          ))}
        </ul>
      </details>
      {plan.skipped.length ? (
        <details>
          <summary className="cursor-pointer select-none">
            跳过 {plan.skipped.length} 项 <Badge tone="gray">不会写入</Badge>
          </summary>
          <ul className="mt-2 max-h-40 space-y-0.5 overflow-y-auto text-xs text-slate-500 dark:text-slate-400">
            {plan.skipped.map((s) => (
              <li key={s.path}>
                <span className="font-mono">{s.path}</span> — {s.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <div className="flex gap-2">
        <Button disabled={busy || (plan.files.length === 0 && plan.overrides.length === 0)} onClick={onApply}>
          {busy ? '导入中…' : '导入'}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  );
}
