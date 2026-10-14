import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/components/PageHeader';
import { cn } from '@/lib/cn';
import { NotifyTab } from '@/pages/settings/NotifyTab';
import { ServerConfigTab } from '@/pages/settings/ServerConfigTab';
import { GameRulesTab } from '@/pages/settings/GameRulesTab';
import { AccountTab } from '@/pages/settings/AccountTab';
import { HistoryTab } from '@/pages/settings/HistoryTab';
import { SystemTab } from './settings/SystemTab';

interface TabDef {
  key: string;
  label: string;
}

const TABS: TabDef[] = [
  { key: 'notify', label: '通知' },
  { key: 'config', label: '服务器配置' },
  { key: 'game', label: '游戏规则' },
  { key: 'account', label: '账号与安全' },
  { key: 'history', label: '修改历史' },
  { key: 'system', label: '系统' },
];

const TAB_TITLE: Record<string, string> = {
  notify: '设置 · 通知',
  config: '设置 · 服务器配置',
  game: '设置 · 游戏规则',
  account: '设置 · 账号与安全',
  history: '设置 · 修改历史',
  system: '设置 · 系统',
};

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get('tab') ?? 'notify';
  const active = TABS.some((t) => t.key === raw) ? raw : 'notify';

  const select = (key: string) => {
    const next = new URLSearchParams(params);
    if (key === 'notify') next.delete('tab');
    else next.set('tab', key);
    setParams(next, { replace: false });
  };

  return (
    <div className="space-y-4">
      <PageHeader title={TAB_TITLE[active]} />

      <div className="border-b border-slate-200 dark:border-slate-800">
        <nav className="-mb-px flex flex-wrap gap-1" role="tablist" aria-label="设置分区">
          {TABS.map((t) => {
            const isActive = t.key === active;
            return (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => select(t.key)}
                className={cn(
                  'border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'border-brand text-brand'
                    : 'border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200',
                )}
              >
                {t.label}
              </button>
            );
          })}
        </nav>
      </div>

      <div role="tabpanel">
        {active === 'notify' ? <NotifyTab /> : null}
        {active === 'config' ? <ServerConfigTab /> : null}
        {active === 'game' ? <GameRulesTab /> : null}
        {active === 'account' ? <AccountTab /> : null}
        {active === 'history' ? <HistoryTab /> : null}
        {active === 'system' ? <SystemTab /> : null}
      </div>
    </div>
  );
}
