import { Button } from '@/components/ui/Button';
import { useTheme, type ThemePref } from '@/hooks/useTheme';

const ORDER: ThemePref[] = ['light', 'dark', 'system'];
const LABEL: Record<ThemePref, string> = {
  light: '亮色',
  dark: '暗色',
  system: '跟随系统',
};

function SunIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4" aria-hidden="true">
      <circle cx="10" cy="10" r="3.5" fill="currentColor" />
      <g stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <path d="M10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M3.9 3.9l1.4 1.4M14.7 14.7l1.4 1.4M16.1 3.9l-1.4 1.4M5.3 14.7l-1.4 1.4" />
      </g>
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4" aria-hidden="true">
      <path d="M15.5 11.6A6 6 0 0 1 8.4 4.5a6 6 0 1 0 7.1 7.1z" />
    </svg>
  );
}

function SystemIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4" aria-hidden="true">
      <rect x="2.5" y="4" width="15" height="9.5" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
      <path d="M7 17h6M10 13.5V17" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

const ICON: Record<ThemePref, () => JSX.Element> = {
  light: SunIcon,
  dark: MoonIcon,
  system: SystemIcon,
};

/** 三态主题切换：亮色 → 暗色 → 跟随系统 → …，点击循环。 */
export function ThemeToggle() {
  const { pref, setPref } = useTheme();
  const Icon = ICON[pref];
  const next = ORDER[(ORDER.indexOf(pref) + 1) % ORDER.length];
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => setPref(next)}
      aria-label={`主题：${LABEL[pref]}，点击切换到${LABEL[next]}`}
      title={`主题：${LABEL[pref]}（点击切换到${LABEL[next]}）`}
    >
      <Icon />
      <span className="hidden sm:inline">{LABEL[pref]}</span>
    </Button>
  );
}
