import { useQuery } from '@tanstack/react-query';
import { configApi } from '@/api';
import type { Capabilities } from '@/api/config';

/**
 * GET /api/capabilities，缓存很久（能力集在一次运行里基本不变）。
 * 加载中或出错时 data 为 undefined —— 调用方应「默认全显示」，避免误藏。
 */
export function useCapabilities() {
  return useQuery({
    queryKey: ['capabilities'],
    queryFn: ({ signal }) => configApi.capabilities(signal),
    staleTime: 10 * 60_000,
    gcTime: 30 * 60_000,
    retry: false,
  });
}

/** 某项特性是否启用；caps 未知（加载/出错）一律返回 true（默认全显示）。 */
export function featureOn(
  caps: Capabilities | undefined,
  key: keyof Capabilities['features'],
): boolean {
  if (!caps) return true;
  return caps.features[key] !== false;
}

/** 某个 mod 是否存在；未知一律 true（默认全显示）。 */
export function modOn(caps: Capabilities | undefined, key: keyof Capabilities['mods']): boolean {
  if (!caps) return true;
  return caps.mods[key] !== false;
}
