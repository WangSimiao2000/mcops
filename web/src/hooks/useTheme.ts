import { useCallback, useEffect, useState } from 'react';

export type ThemePref = 'light' | 'dark' | 'system';
const STORAGE_KEY = 'mcops-theme';

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function resolve(pref: ThemePref): boolean {
  if (pref === 'dark') return true;
  if (pref === 'light') return false;
  return systemPrefersDark();
}

function readPref(): ThemePref {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === 'light' || raw === 'dark' ? raw : 'system';
}

/** Theme controller: resolves the effective dark flag and persists the choice. */
export function useTheme(): {
  pref: ThemePref;
  isDark: boolean;
  setPref: (p: ThemePref) => void;
  toggle: () => void;
} {
  const [pref, setPrefState] = useState<ThemePref>(() => readPref());
  const [isDark, setIsDark] = useState<boolean>(() => resolve(readPref()));

  useEffect(() => {
    const dark = resolve(pref);
    setIsDark(dark);
    document.documentElement.classList.toggle('dark', dark);
    if (pref === 'system') {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, pref);
    }
  }, [pref]);

  useEffect(() => {
    if (pref !== 'system') return;
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => {
      const dark = systemPrefersDark();
      setIsDark(dark);
      document.documentElement.classList.toggle('dark', dark);
    };
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [pref]);

  const setPref = useCallback((p: ThemePref) => setPrefState(p), []);
  const toggle = useCallback(() => {
    setPrefState((prev) => (resolve(prev) ? 'light' : 'dark'));
  }, []);

  return { pref, isDark, setPref, toggle };
}
