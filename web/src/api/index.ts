import { api as realApi } from './client';
import { configApi as configApiImpl } from './config';
import type { ConsoleSocket, ConsoleSocketHandlers } from './consoleSocket';
import { mockApi } from './mockApi';
import { createMockConsoleSocket } from './mockConsoleSocket';
import { createConsoleSocket as createRealConsoleSocket } from './realConsoleSocket';

export const IS_MOCK = import.meta.env.VITE_MOCK === '1';

// A single surface the app imports; swaps to the in-memory mock when VITE_MOCK=1.
export const api = IS_MOCK ? mockApi : realApi;

// P1 配置接口。config.ts 内部已按 VITE_MOCK 在真实 fetch 与内存 mock 之间切换。
export const configApi = configApiImpl;

export function createConsoleSocket(handlers: ConsoleSocketHandlers): ConsoleSocket {
  return IS_MOCK ? createMockConsoleSocket(handlers) : createRealConsoleSocket(handlers);
}

export * from './types';
