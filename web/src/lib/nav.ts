export interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

/** 侧栏按语义分段。顺序即渲染顺序。 */
export interface NavSection {
  heading: string;
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    heading: '运行',
    items: [
      { to: '/', label: '总览', end: true },
      { to: '/console', label: '控制台' },
      { to: '/ops', label: '运维任务' },
      { to: '/backups', label: '备份' },
      { to: '/mods', label: 'Mod 与升级' },
    ],
  },
  {
    heading: '安全',
    items: [
      { to: '/security', label: '安全' },
      { to: '/network', label: '网络' },
      { to: '/players', label: '玩家' },
      { to: '/whitelist', label: '白名单' },
    ],
  },
  {
    heading: '集成与设置',
    items: [
      { to: '/bot', label: 'QQ 机器人' },
      { to: '/logs', label: '日志' },
      { to: '/settings', label: '设置' },
    ],
  },
];

/** 平铺的全部导航项（标题解析、路由遍历用）。 */
export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);

/** 路由 -> 需要的能力开关；该开关为 false 时隐藏该导航项。 */
const NAV_FEATURE_GATE: Record<string, 'bot'> = {
  '/bot': 'bot',
};

/**
 * 按 capabilities 过滤导航：features.bot=false 隐藏「QQ 机器人」。
 * caps 为 undefined（加载中/出错）时默认显示全部。空 section 被丢弃。
 */
export function filterNavSections(
  caps: { features: Record<string, boolean> } | undefined,
  sections: NavSection[] = NAV_SECTIONS,
): NavSection[] {
  if (!caps) return sections;
  const out: NavSection[] = [];
  for (const section of sections) {
    const items = section.items.filter((item) => {
      const gate = NAV_FEATURE_GATE[item.to];
      if (!gate) return true;
      return caps.features[gate] !== false;
    });
    if (items.length > 0) out.push({ ...section, items });
  }
  return out;
}

const APP_NAME = 'mc-ops';

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** Browser tab title for a route, so several open tabs can be told apart. */
export function pageTitle(pathname: string): string {
  const path = pathname.replace(/\/+$/, '') || '/';
  const detail = /^\/(players|ips)\/(.+)$/.exec(path);
  if (detail) {
    const kind = detail[1] === 'players' ? '玩家' : 'IP';
    return `${safeDecode(detail[2])} · ${kind} · ${APP_NAME}`;
  }
  const item = NAV_ITEMS.find((n) => n.to === path);
  return item ? `${item.label} · ${APP_NAME}` : APP_NAME;
}
