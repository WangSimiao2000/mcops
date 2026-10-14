import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import QRCode from 'qrcode';
import { api } from '@/api';
import type { SecretItem, TotpSetupResponse } from '@/api/config';
import { ApiError } from '@/api/http';
import { useAuth } from '@/auth/useAuth';
import { APPLY_LABEL, APPLY_TONE } from '@/lib/schemaForm';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';

export function AccountTab() {
  return (
    <div className="space-y-4">
      <PasswordCard />
      <TotpCard />
      <SecretsCard />
    </div>
  );
}

// ---- 修改密码 ---------------------------------------------------------------

function PasswordCard() {
  const { logout } = useAuth();
  const [oldPw, setOldPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);

  const localError =
    newPw && newPw.length < 12
      ? '新密码至少 12 位'
      : confirm && newPw !== confirm
        ? '两次输入的新密码不一致'
        : null;

  const mut = useMutation({
    mutationFn: () => api.changePassword(oldPw, newPw),
    onSuccess: () => {
      // 会话已被吊销，跳转登录页。
      void logout();
    },
    onError: (e) => setError(e instanceof ApiError ? e.detail : '修改失败'),
  });

  const canSubmit = oldPw !== '' && newPw.length >= 12 && newPw === confirm && !mut.isPending;

  return (
    <Card>
      <CardHeader title="修改密码" />
      <CardBody className="space-y-3">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          修改后所有会话（含当前）将被吊销，需用新密码重新登录。
        </p>
        <Input
          label="当前密码"
          type="password"
          autoComplete="current-password"
          value={oldPw}
          onChange={(e) => setOldPw(e.target.value)}
        />
        <Input
          label="新密码（至少 12 位）"
          type="password"
          autoComplete="new-password"
          value={newPw}
          onChange={(e) => setNewPw(e.target.value)}
        />
        <Input
          label="确认新密码"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          error={localError ?? undefined}
        />
        {error ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        ) : null}
        <div>
          <Button
            size="sm"
            disabled={!canSubmit}
            onClick={() => {
              setError(null);
              mut.mutate();
            }}
          >
            {mut.isPending ? '提交中…' : '修改密码'}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

// ---- 两步验证（TOTP） -------------------------------------------------------

function TotpCard() {
  const qc = useQueryClient();
  const statusQuery = useQuery({
    queryKey: ['totp'],
    queryFn: ({ signal }) => api.totpStatus(signal),
    retry: false,
  });
  const [setup, setSetup] = useState<TotpSetupResponse | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [disablePw, setDisablePw] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!setup) {
      setQrDataUrl(null);
      return;
    }
    let cancelled = false;
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 180 })
      .then((url) => {
        if (!cancelled) setQrDataUrl(url);
      })
      .catch(() => {
        if (!cancelled) setQrDataUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [setup]);

  const setupMut = useMutation({
    mutationFn: () => api.totpSetup(),
    onSuccess: (r) => {
      setError(null);
      setSetup(r);
    },
    onError: (e) => setError(e instanceof ApiError ? e.detail : '无法开始设置'),
  });
  const confirmMut = useMutation({
    mutationFn: () => api.totpConfirm(code),
    onSuccess: () => {
      setSetup(null);
      setCode('');
      void qc.invalidateQueries({ queryKey: ['totp'] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.detail : '验证码不正确'),
  });
  const disableMut = useMutation({
    mutationFn: () => api.totpDisable(disablePw),
    onSuccess: () => {
      setDisablePw('');
      void qc.invalidateQueries({ queryKey: ['totp'] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.detail : '关闭失败'),
  });

  const enabled = statusQuery.data?.enabled ?? false;

  return (
    <Card>
      <CardHeader
        title="两步验证（TOTP）"
        action={<Badge tone={enabled ? 'green' : 'gray'}>{enabled ? '已开启' : '未开启'}</Badge>}
      />
      <CardBody className="space-y-3">
        {error ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        ) : null}

        {enabled ? (
          <div className="space-y-2">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              关闭两步验证需输入当前登录密码。
            </p>
            <div className="flex flex-wrap items-end gap-2">
              <Input
                label="登录密码"
                type="password"
                autoComplete="current-password"
                value={disablePw}
                onChange={(e) => setDisablePw(e.target.value)}
                className="w-48"
              />
              <Button
                size="sm"
                variant="danger"
                disabled={disablePw === '' || disableMut.isPending}
                onClick={() => {
                  setError(null);
                  disableMut.mutate();
                }}
              >
                {disableMut.isPending ? '关闭中…' : '关闭两步验证'}
              </Button>
            </div>
          </div>
        ) : setup ? (
          <div className="space-y-3">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              用验证器 App 扫码（或手动输入密钥），再填入 6 位验证码确认。
            </p>
            <div className="flex flex-wrap items-center gap-4">
              {qrDataUrl ? (
                <img
                  src={qrDataUrl}
                  alt="TOTP 二维码"
                  width={180}
                  height={180}
                  className="rounded-md border border-slate-200 bg-white p-2 dark:border-slate-700"
                />
              ) : (
                <div className="flex h-[180px] w-[180px] items-center justify-center rounded-md border border-slate-200 text-xs text-slate-400 dark:border-slate-700">
                  生成二维码…
                </div>
              )}
              <div className="space-y-1 text-sm">
                <div className="text-slate-500 dark:text-slate-400">手动密钥</div>
                <code className="block break-all font-mono text-xs text-slate-700 dark:text-slate-300">
                  {setup.secret}
                </code>
              </div>
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <Input
                label="6 位验证码"
                inputMode="numeric"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                className="w-32"
              />
              <Button
                size="sm"
                disabled={code.length !== 6 || confirmMut.isPending}
                onClick={() => {
                  setError(null);
                  confirmMut.mutate();
                }}
              >
                {confirmMut.isPending ? '确认中…' : '确认开启'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSetup(null)}>
                取消
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              开启后，登录时除密码外还需输入验证器生成的 6 位动态码。
            </p>
            <Button
              size="sm"
              disabled={setupMut.isPending}
              onClick={() => {
                setError(null);
                setupMut.mutate();
              }}
            >
              {setupMut.isPending ? '准备中…' : '开启两步验证'}
            </Button>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// ---- 机密 -------------------------------------------------------------------

function SecretsCard() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ['secrets'],
    queryFn: ({ signal }) => api.secrets(signal),
    retry: false,
  });
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const mut = useMutation({
    mutationFn: () => {
      const toSend: Record<string, string> = {};
      for (const [k, v] of Object.entries(values)) if (v !== '') toSend[k] = v;
      return api.updateSecrets(toSend);
    },
    onSuccess: () => {
      setError(null);
      setSaved(true);
      setValues({});
      void qc.invalidateQueries({ queryKey: ['secrets'] });
    },
    onError: (e) => {
      setSaved(false);
      setError(e instanceof ApiError ? e.detail : '保存失败');
    },
  });

  const items = query.data?.items ?? [];
  const anyTyped = Object.values(values).some((v) => v !== '');

  return (
    <Card>
      <CardHeader title="机密" />
      <CardBody className="space-y-3">
        <p className="text-sm text-slate-600 dark:text-slate-300">
          机密只写不回显。留空表示不修改；输入新值后保存即覆盖。
        </p>
        <ul className="space-y-3">
          {items.map((it: SecretItem) => (
            <li key={it.key} className="space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-slate-700 dark:text-slate-200">
                  {it.title}
                </span>
                <Badge tone={it.set ? 'green' : 'gray'}>{it.set ? '已设置' : '未设置'}</Badge>
                {it.apply ? <Badge tone={APPLY_TONE[it.apply]}>{APPLY_LABEL[it.apply]}</Badge> : null}
              </div>
              {it.editable ? (
                <input
                  type="password"
                  autoComplete="new-password"
                  placeholder={it.set ? '留空表示不修改' : '输入以设置'}
                  aria-label={it.title}
                  value={values[it.key] ?? ''}
                  onChange={(e) => {
                    setSaved(false);
                    setValues((v) => ({ ...v, [it.key]: e.target.value }));
                  }}
                  className="w-full max-w-md rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand focus:ring-0 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                />
              ) : (
                <p className="text-xs text-slate-500 dark:text-slate-400">{it.note ?? '不可通过面板修改。'}</p>
              )}
            </li>
          ))}
        </ul>
        {error ? (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400">
            {error}
          </p>
        ) : null}
        {saved ? (
          <p role="status" className="text-sm text-green-700 dark:text-green-400">
            已保存。
          </p>
        ) : null}
        <div>
          <Button
            size="sm"
            disabled={!anyTyped || mut.isPending}
            onClick={() => {
              setSaved(false);
              mut.mutate();
            }}
          >
            {mut.isPending ? '保存中…' : '保存机密'}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
