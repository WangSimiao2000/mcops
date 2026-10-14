/** Read a cookie value by name from document.cookie. Returns null if absent. */
export function readCookie(name: string): string | null {
  const target = `${name}=`;
  const parts = document.cookie ? document.cookie.split('; ') : [];
  for (const part of parts) {
    if (part.startsWith(target)) {
      return decodeURIComponent(part.slice(target.length));
    }
  }
  return null;
}

export const CSRF_COOKIE = 'mcops_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';
