import { Suspense, useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { AlertBell } from '@/components/AlertBell';
import { AlertToaster } from '@/components/AlertToaster';
import { ConfigPendingBanner } from '@/components/ConfigPendingBanner';
import { ConnectionIndicator } from '@/components/ConnectionIndicator';
import { ThemeToggle } from '@/components/ThemeToggle';

/** 改了代码再对外提供服务时，请把这里换成你自己的源码地址。 */
const SOURCE_URL = 'https://github.com/WangSimiao2000/mcops';
import { Button } from '@/components/ui/Button';
import { useConsole } from '@/console/useConsole';
import { useCapabilities } from '@/lib/capabilities';
import { cn } from '@/lib/cn';
import { filterNavSections, NAV_SECTIONS, pageTitle } from '@/lib/nav';

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { data: caps } = useCapabilities();
  const sections = filterNavSections(caps, NAV_SECTIONS);
  return (
    <nav className="flex-1 space-y-4 overflow-y-auto px-2 pb-4" aria-label="主导航">
      {sections.map((section) => (
        <div key={section.heading} className="space-y-1">
          <h2 className="px-3 pb-0.5 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
            {section.heading}
          </h2>
          {section.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={onNavigate}
              className={({ isActive }) =>
                cn(
                  'block rounded-md px-3 py-2 text-sm font-medium',
                  isActive
                    ? 'bg-brand text-brand-fg'
                    : 'text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800',
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

function PageFallback() {
  return (
    <div role="status" className="p-4 text-sm text-slate-500 dark:text-slate-400">
      加载中…
    </div>
  );
}

export function AppLayout() {
  const { user, logout } = useAuth();
  const { connection } = useConsole();
  const { pathname } = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    document.title = pageTitle(pathname);
    setDrawerOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawerOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawerOpen]);

  const brand = (
    <div className="px-4 py-4 text-base font-semibold text-slate-900 dark:text-slate-100">mc-ops</div>
  );

  // AGPL-3.0 第 13 条：通过网络使用本程序的用户应能拿到源代码。
  const sourceLink = (
    <a
      href={SOURCE_URL}
      target="_blank"
      rel="noreferrer"
      className="mt-auto px-4 py-3 text-xs text-slate-500 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 dark:text-slate-400 dark:hover:text-slate-200"
    >
      源代码（AGPL-3.0）
    </a>
  );

  return (
    <div className="flex h-screen">
      {/* Desktop: sticky full-height sidebar. Below md it collapses into the drawer below. */}
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 md:flex">
        {brand}
        <NavList />
        {sourceLink}
      </aside>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            aria-label="关闭菜单"
            className="absolute inset-0 bg-slate-900/50"
            onClick={() => setDrawerOpen(false)}
          />
          <aside
            id="mobile-nav"
            className="relative flex h-full w-64 max-w-[80%] flex-col border-r border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-900"
          >
            {brand}
            <NavList onNavigate={() => setDrawerOpen(false)} />
            {sourceLink}
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="relative z-40 flex items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900 sm:px-4 sm:py-3">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              className="rounded-md p-2 text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800 md:hidden"
              aria-label="打开菜单"
              aria-expanded={drawerOpen}
              aria-controls="mobile-nav"
              onClick={() => setDrawerOpen(true)}
            >
              <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5" aria-hidden="true">
                <path
                  fillRule="evenodd"
                  d="M3 5h14a1 1 0 010 2H3a1 1 0 010-2zm0 4h14a1 1 0 010 2H3a1 1 0 010-2zm0 4h14a1 1 0 010 2H3a1 1 0 010-2z"
                  clipRule="evenodd"
                />
              </svg>
            </button>
            <ConnectionIndicator state={connection} />
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <AlertBell />
            <ThemeToggle />
            <span className="hidden text-sm text-slate-500 dark:text-slate-400 sm:inline">{user}</span>
            <Button variant="secondary" size="sm" onClick={() => void logout()}>
              <span>
                退出<span className="hidden sm:inline">登录</span>
              </span>
            </Button>
          </div>
        </header>
        <ConfigPendingBanner />
        <main className="min-w-0 flex-1 overflow-auto p-3 sm:p-4">
          <Suspense fallback={<PageFallback />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      <AlertToaster />
    </div>
  );
}
