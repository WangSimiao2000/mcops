import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { api, IS_MOCK } from '@/api';
import { ApiError } from '@/api/http';
import { useAuth } from '@/auth/useAuth';
import { Button } from '@/components/ui/Button';
import { Card, CardBody } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';

export function LoginPage() {
  const { markAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [password, setPassword] = useState('');
  const [totp, setTotp] = useState('');
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? '/';

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const me = await api.login(password, needTotp ? totp : undefined);
      markAuthenticated(me.user);
      navigate(from, { replace: true });
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401 && err.detail === 'totp_required') {
          setNeedTotp(true);
          setError('请输入两步验证动态码');
        } else if (err.status === 429) {
          setError('尝试过于频繁，请稍后再试');
        } else {
          setError(err.detail || '登录失败');
        }
      } else {
        setError('网络错误，请重试');
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardBody>
          <h1 className="mb-1 text-lg font-semibold text-slate-900 dark:text-slate-100">
            mc-ops 运维面板
          </h1>
          <p className="mb-5 text-sm text-slate-500 dark:text-slate-400">
            请使用管理员密码登录{IS_MOCK ? '（mock 模式密码：admin）' : ''}
          </p>
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <Input
              label="密码"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              required
            />
            {needTotp ? (
              <Input
                label="动态验证码 (TOTP)"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={totp}
                onChange={(e) => setTotp(e.target.value)}
                placeholder="6 位数字"
              />
            ) : null}
            {error ? (
              <p role="alert" className="text-sm text-red-600 dark:text-red-400">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting || password === ''}>
              {submitting ? '登录中…' : '登录'}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
