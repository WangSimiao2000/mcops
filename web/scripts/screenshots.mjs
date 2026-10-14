// Visual check of every page in mock mode: light + dark, desktop + phone widths.
//
//   npx playwright install chromium   # once
//   npm run screenshots -- [outDir]   # default: ./screenshots (git-ignored)
//
// Navigates client-side after one login (the mock session is in memory).
// Starts its own mock dev server on 127.0.0.1 (no backend needed), logs in with the mock
// password, and writes one full-page PNG per page/theme/viewport plus report.json listing
// console errors and elements wider than the viewport (horizontal overflow).
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright';
import { createServer } from 'vite';

const OUT = resolve(process.argv[2] ?? 'screenshots');
const PAGES = [
  ['login', '/login'],
  ['overview', '/'],
  ['console', '/console'],
  ['players', '/players'],
  ['player-detail', '/players/OpAdmin'],
  ['ip-detail', '/ips/203.0.113.74'],
  ['security', '/security'],
  ['bot', '/bot'],
  ['whitelist', '/whitelist'],
  ['ops', '/ops'],
  ['backups', '/backups'],
  ['mods', '/mods'],
  ['logs', '/logs'],
  ['network', '/network'],
  ['settings', '/settings'],
  ['settings-config', '/settings?tab=config'],
  ['settings-game', '/settings?tab=game'],
  ['settings-account', '/settings?tab=account'],
  ['settings-history', '/settings?tab=history'],
];
const VIEWPORTS = [
  ['desktop', { width: 1440, height: 900 }],
  ['mobile', { width: 390, height: 844 }],
];
const THEMES = ['light', 'dark'];

process.env.VITE_MOCK = '1';
const server = await createServer({
  configFile: resolve('vite.config.ts'),
  server: { host: '127.0.0.1', port: 5199, strictPort: true },
  logLevel: 'warn',
});
await server.listen();
const base = 'http://127.0.0.1:5199';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const report = [];
try {
  for (const theme of THEMES) {
    for (const [vpName, viewport] of VIEWPORTS) {
      const ctx = await browser.newContext({ viewport, colorScheme: theme });
      await ctx.addInitScript((t) => localStorage.setItem('mcops-theme', t), theme);
      const page = await ctx.newPage();
      let errors = [];
      page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
      page.on('pageerror', (e) => errors.push(String(e)));

      await page.goto(`${base}/login`);
      await page.screenshot({ path: `${OUT}/login-${vpName}-${theme}.png`, fullPage: true });
      await page.getByLabel(/密码/).first().fill('admin');
      await page.getByRole('button', { name: /登录/ }).click();
      await page.waitForURL(`${base}/`);

      for (const [name, path] of PAGES.slice(1)) {
        errors = [];
        // Client-side navigation: a full reload would reset the in-memory mock session.
        await page.evaluate((to) => {
          window.history.pushState({}, '', to);
          window.dispatchEvent(new PopStateEvent('popstate'));
        }, path);
        await page.waitForURL(`${base}${path}`);
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(name === 'console' ? 2500 : 600); // mock latency, charts, terminal
        const overflow = await page.evaluate(() => {
          const vw = document.documentElement.clientWidth;
          const out = [];
          for (const el of document.querySelectorAll('main *')) {
            const r = el.getBoundingClientRect();
            if (r.right > vw + 1 && r.width > 0) {
              // skip things inside a horizontal scroller (intended)
              let p = el.parentElement;
              let scrolled = false;
              while (p && p.tagName !== 'MAIN') {
                const ox = getComputedStyle(p).overflowX;
                if (ox === 'auto' || ox === 'scroll') { scrolled = true; break; }
                p = p.parentElement;
              }
              if (!scrolled) out.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 60)} right=${Math.round(r.right)}`);
            }
          }
          return { vw, docWidth: document.documentElement.scrollWidth, items: out.slice(0, 8) };
        });
        const file = `${name}-${vpName}-${theme}.png`;
        // The shell is h-screen with an internally scrolling <main>; unroll it so the
        // full-page capture includes everything below the fold.
        const unroll = await page.addStyleTag({
          content: '.h-screen{height:auto!important;min-height:100vh} main{overflow:visible!important}',
        });
        await page.screenshot({ path: `${OUT}/${file}`, fullPage: true });
        await unroll.evaluate((el) => el.remove());
        report.push({ page: name, viewport: vpName, theme, file, errors, overflow });
      }
      await ctx.close();
    }
  }
} finally {
  await browser.close();
  await server.close();
}
writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 2));
const bad = report.filter((r) => r.errors.length || r.overflow.items.length);
console.log(`${report.length} screenshots -> ${OUT}; ${bad.length} with console errors or overflow`);
for (const r of bad) {
  console.log(`- ${r.page} ${r.viewport} ${r.theme}: errors=${r.errors.length} overflow=${r.overflow.items.length}`);
}
