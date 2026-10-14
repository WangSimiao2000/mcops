import { lazy } from 'react';
import type { ComponentType } from 'react';

const RELOAD_FLAG = 'mcops-chunk-reload';

/**
 * React.lazy for a named page export. A deploy replaces the hashed chunk files, so a tab
 * opened before it fails its next dynamic import; reload once to pick up the new index.html.
 * The session flag stops a reload loop when the chunk is genuinely unreachable.
 */
export async function loadPage<M extends Record<string, unknown>, K extends keyof M & string>(
  load: () => Promise<M>,
  name: K,
  reload: () => void = () => window.location.reload(),
): Promise<{ default: ComponentType }> {
  try {
    const mod = await load();
    sessionStorage.removeItem(RELOAD_FLAG);
    return { default: mod[name] as ComponentType };
  } catch (err) {
    if (!sessionStorage.getItem(RELOAD_FLAG)) {
      sessionStorage.setItem(RELOAD_FLAG, '1');
      reload();
      // Keep Suspense pending while the page reloads.
      return new Promise<never>(() => {});
    }
    throw err;
  }
}

export function lazyPage<M extends Record<string, unknown>, K extends keyof M & string>(
  load: () => Promise<M>,
  name: K,
) {
  return lazy(() => loadPage(load, name));
}
