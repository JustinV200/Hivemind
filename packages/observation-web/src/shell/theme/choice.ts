/**
 * The theme: which of light and dark the page shows, stored per viewer and applied to `<html>`.
 *
 * tokens.css honours `data-theme` on `<html>`: `dark` forces the night hive, `light` forces the
 * day, and no attribute follows the system. The viewer's choice is kept in `localStorage` so it
 * survives a reload, and applied before the app mounts (`applyStoredTheme` in main.tsx) so the
 * first paint is already the chosen theme. Storage can be absent or refused (a private window),
 * so every read and write is guarded and the page then simply follows the system.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/theme/`. Read by `toggle.tsx`; `applyStoredTheme` is
 *   called once by `src/main.tsx`.
 *
 * Key invariants:
 *   - The attribute on `<html>` is the single source of truth once applied; the hook reads it.
 *   - Only `light` and `dark` are ever stored or applied; anything else is treated as unset.
 *
 * See Also:
 *   - src/styles/tokens.css for how the attribute selects the palette.
 *   - docs/observation/design.md section 3.
 */
import { useCallback, useSyncExternalStore } from 'react';

export type Theme = 'light' | 'dark';

const KEY = 'hive.theme';
const listeners = new Set<() => void>();

const isTheme = (value: unknown): value is Theme => value === 'light' || value === 'dark';

// The stored choice, or null when none is stored or storage cannot be read.
export function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(KEY);
    return isTheme(value) ? value : null;
  } catch {
    return null;
  }
}

// What the page is showing: the attribute if set, else the system's preference.
export function currentTheme(): Theme {
  const set = document.documentElement.dataset.theme;
  if (isTheme(set)) return set;
  const dark =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches;
  return dark ? 'dark' : 'light';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    // Storage refused: the page still shows the theme until it is reloaded.
  }
  listeners.forEach((wake) => wake());
}

// Called once before the first render, so the chosen theme is on the page from the first paint.
export function applyStoredTheme(): void {
  const stored = storedTheme();
  if (stored !== null) document.documentElement.dataset.theme = stored;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => 'light' as Theme);
  return [theme, useCallback((next: Theme) => applyTheme(next), [])];
}
