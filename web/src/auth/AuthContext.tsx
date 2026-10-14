import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { AuthContext } from './useAuth';
import type { AuthStatus, AuthContextValue } from './useAuth';
import { api } from '@/api';
import { ApiError } from '@/api/http';
import { setUnauthorizedHandler } from '@/api/http';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<string | null>(null);
  const mounted = useRef(true);

  // Register the global 401 handler so any API call can drop us to anonymous,
  // which flips the router over to the login route.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (mounted.current) {
        setStatus('anonymous');
        setUser(null);
      }
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  // Bootstrap: ask the backend who we are.
  useEffect(() => {
    mounted.current = true;
    const ctrl = new AbortController();
    api
      .me(ctrl.signal)
      .then((me) => {
        if (!mounted.current) return;
        setUser(me.user);
        setStatus('authenticated');
      })
      .catch((err: unknown) => {
        if (!mounted.current) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        if (err instanceof ApiError && err.status === 401) {
          setStatus('anonymous');
        } else {
          // Network / server error: treat as anonymous so the user can retry login.
          setStatus('anonymous');
        }
        setUser(null);
      });
    return () => {
      mounted.current = false;
      ctrl.abort();
    };
  }, []);

  const markAuthenticated = useCallback((u: string) => {
    setUser(u);
    setStatus('authenticated');
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Ignore; we drop local state regardless.
    }
    setUser(null);
    setStatus('anonymous');
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, markAuthenticated, logout }),
    [status, user, markAuthenticated, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
