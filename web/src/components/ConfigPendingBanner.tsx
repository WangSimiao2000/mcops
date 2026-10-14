import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';

type Phase = 'idle' | 'restarting' | 'error';

/**
 * 全局配置待应用横幅：所有页面可见。
 * - agent 类待应用项 -> 横幅 +「立即应用（重启 agent）」。
 * - mc 类 -> 下方低调提示「N 项将在下次开服生效」。
 * 点「立即应用」-> ConfirmDialog -> POST /api/system/restart-agent -> 轮询
 *   /api/health 直到 ok（最多 60s）-> 刷新所有查询。
 * 409（开发模式不在 systemd 下）-> 提示手动重启。
 */
export function ConfigPendingBanner() {
  const qc = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [message, setMessage] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ['config'],
    queryFn: ({ signal }) => api.config(signal),
    staleTime: 5000,
    retry: false,
  });

  const pending = data?.pending ?? [];
  const agentCount = pending.filter((p) => p.apply === 'agent').length;
  const mcCount = pending.filter((p) => p.apply === 'mc').length;

  if (agentCount === 0 && mcCount === 0) return null;

  async function pollHealth(deadlineMs: number): Promise<boolean> {
    const start = Date.now();
    // 先等一下让 systemd 把 agent 停掉，避免立刻读到旧的 ok。
    await new Promise((r) => setTimeout(r, 1500));
    while (Date.now() - start < deadlineMs) {
      try {
        const h = await api.health();
        if (h.status === 'ok') return true;
      } catch {
        // 重启窗口内连接会断，忽略继续轮询。
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    return false;
  }

  async function doRestart() {
    setConfirmOpen(false);
    setPhase('restarting');
    setMessage(null);
    try {
      await api.restartAgent();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setPhase('error');
        setMessage('当前不在 systemd 下运行（开发模式），请手动重启 agent 使改动生效。');
        return;
      }
      setPhase('error');
      setMessage(e instanceof ApiError ? e.detail : '重启请求失败。');
      return;
    }
    const ok = await pollHealth(60_000);
    if (ok) {
      await qc.invalidateQueries();
      setPhase('idle');
      setMessage(null);
    } else {
      setPhase('error');
      setMessage('重启后 60 秒内未恢复，请检查 agent 状态或手动刷新。');
    }
  }

  const restarting = phase === 'restarting';

  return (
    <div className="border-b border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-900/50 dark:bg-amber-900/20 sm:px-4">
      {agentCount > 0 ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span role="status" className="text-sm text-amber-800 dark:text-amber-200">
            有 {agentCount} 项配置待应用（重启 agent，不影响在线玩家）
          </span>
          <Button
            size="sm"
            variant="primary"
            disabled={restarting}
            onClick={() => setConfirmOpen(true)}
          >
            {restarting ? '正在重启 agent…' : '立即应用'}
          </Button>
          {mcCount > 0 ? (
            <span className="text-xs text-amber-700/80 dark:text-amber-300/80">
              另有 {mcCount} 项将在下次开服生效
            </span>
          ) : null}
        </div>
      ) : (
        <span className="text-xs text-amber-700 dark:text-amber-300">
          有 {mcCount} 项配置将在下次开服生效（05:00 定时重启或手动重启）
        </span>
      )}

      {message ? (
        <p
          role="alert"
          className="mt-1 text-xs text-red-700 dark:text-red-400"
        >
          {message}
        </p>
      ) : null}

      <ConfirmDialog
        open={confirmOpen}
        danger={false}
        title="立即重启 agent 应用配置？"
        description="agent 会在后台重启，MC 服务器与在线玩家不受影响。重启期间面板会短暂断开，恢复后自动刷新。"
        confirmLabel="重启 agent"
        onConfirm={() => void doRestart()}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
}
