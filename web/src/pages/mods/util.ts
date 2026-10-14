import { ApiError } from '@/api/http';
import type { ConfigFile } from '@/api/modstore';

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error && e.message) return e.message;
  return '操作失败';
}

/** 浏览器端先做一遍 JSON 语法检查，TOML/YAML 交给后端校验。 */
export function localSyntaxError(format: ConfigFile['format'], text: string): string | null {
  if (format !== 'json') return null;
  try {
    JSON.parse(text);
    return null;
  } catch (e) {
    return `JSON 语法错误：${e instanceof Error ? e.message : String(e)}`;
  }
}
