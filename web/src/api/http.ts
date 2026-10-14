import { CSRF_COOKIE, CSRF_HEADER, readCookie } from './cookies';

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export type UnauthorizedHandler = () => void;

let onUnauthorized: UnauthorizedHandler | null = null;

/**
 * Register a global handler invoked whenever any request returns 401.
 * The app uses this to redirect to the login page.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

export const API_BASE = '/api';

interface RequestOptions {
  /** 原样发送的二进制请求体（与 body 互斥） */
  rawBody?: Blob | ArrayBuffer;
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  /** When true, a 401 does NOT trigger the global unauthorized handler. */
  suppressUnauthorized?: boolean;
}

/**
 * Core fetch wrapper.
 * - Always sends credentials (cookie session).
 * - Attaches the CSRF header (from the mcops_csrf cookie) to every non-GET request.
 * - On 401, invokes the global unauthorized handler (unless suppressed) and throws.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  const headers: Record<string, string> = { Accept: 'application/json' };

  if (method !== 'GET' && method !== 'HEAD') {
    const token = readCookie(CSRF_COOKIE);
    if (token) {
      headers[CSRF_HEADER] = token;
    }
  }

  let bodyInit: BodyInit | undefined;
  if (options.rawBody !== undefined) {
    // 二进制上传（jar / mrpack）：原样发送，不做 JSON 编码
    headers['Content-Type'] = 'application/octet-stream';
    bodyInit = options.rawBody;
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    bodyInit = JSON.stringify(options.body);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    ...(bodyInit !== undefined ? { body: bodyInit } : {}),
    ...(options.signal ? { signal: options.signal } : {}),
  });

  if (res.status === 401 && !options.suppressUnauthorized) {
    onUnauthorized?.();
  }

  if (res.status === 204) {
    return undefined as T;
  }

  let payload: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!res.ok) {
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : typeof payload === 'string'
          ? payload
          : `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }

  return payload as T;
}
