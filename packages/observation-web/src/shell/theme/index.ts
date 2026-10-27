/**
 * The theme package's face: the stored light or dark choice and the button that switches it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/theme/`. `applyStoredTheme` is called by `src/main.tsx`
 *   before the first render; `ThemeToggle` is drawn by the top bar.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/styles/tokens.css for how `data-theme` selects the palette.
 */
export { applyStoredTheme, applyTheme, currentTheme, storedTheme, useTheme } from './choice';
export type { Theme } from './choice';
export { ThemeToggle } from './toggle';
