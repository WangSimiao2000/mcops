import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ThemeToggle } from '@/components/ThemeToggle';

const STORAGE_KEY = 'mcops-theme';

beforeEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove('dark');
  // jsdom lacks matchMedia; default to light so 'system' resolves deterministically.
  vi.stubGlobal(
    'matchMedia',
    (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }) as unknown as MediaQueryList,
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ThemeToggle', () => {
  it('shows an SVG icon, not an emoji', () => {
    render(<ThemeToggle />);
    const btn = screen.getByRole('button');
    expect(btn.querySelector('svg')).not.toBeNull();
    expect(btn.textContent ?? '').not.toMatch(/🌙|☀️/);
  });

  it('cycles 跟随系统 → 亮色 → 暗色 → 跟随系统', () => {
    render(<ThemeToggle />);
    const btn = screen.getByRole('button');
    // No stored pref ⇒ 跟随系统 (system) initial.
    expect(btn).toHaveAttribute('aria-label', expect.stringContaining('主题：跟随系统'));

    fireEvent.click(btn); // system → light
    expect(screen.getByRole('button')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('主题：亮色'),
    );
    expect(localStorage.getItem(STORAGE_KEY)).toBe('light');

    fireEvent.click(screen.getByRole('button')); // light → dark
    expect(screen.getByRole('button')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('主题：暗色'),
    );
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);

    fireEvent.click(screen.getByRole('button')); // dark → system
    expect(screen.getByRole('button')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('主题：跟随系统'),
    );
    // 'system' clears the stored key (resolves live from the OS).
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });
});
