import { useSearchParams } from 'react-router-dom';

import type { TabDef } from '@/components/Tabs';

/** URL `?tab=` 驱动的标签页。默认标签不写进 URL。 */
export function useUrlTab(tabs: TabDef[], fallback: string): [string, (key: string) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get('tab') ?? fallback;
  const active = tabs.some((t) => t.key === raw) ? raw : fallback;
  const select = (key: string) => {
    const next = new URLSearchParams(params);
    if (key === fallback) next.delete('tab');
    else next.set('tab', key);
    setParams(next, { replace: false });
  };
  return [active, select];
}
