/**
 * `useMinWidth(px)`: whether the page is at least this wide, as React state that follows resizes.
 *
 * Most width rules are stylesheet alone, but a few things move between elements rather than
 * restyle: the device name and the log out button sit in the top bar on a rail and in the nav's
 * More panel on a phone, and a table's "Sort by" control stands in for the header row only
 * while the rows are cards. Rendering both and hiding one would leave two controls with the same
 * name in the page for a screen reader, so a view asks this hook which one to draw.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by `src/shell/` and `src/views/fleet/`.
 *
 * Key invariants:
 *   - Where `matchMedia` is missing (jsdom in tests) every width counts as reached, so the layout
 *     that holds every control is the one the tests see.
 *   - The breakpoints passed in are the stylesheet's (design.md section 2) and must stay in step.
 *
 * See Also:
 *   - src/styles/shell.css and the views' stylesheets for the rules that move with it.
 */
import { useCallback, useSyncExternalStore } from 'react';

export const RAIL_MIN = 720; // The nav becomes a rail and the bar gains room (design.md 2).
export const NARROW_TABLE_MIN = 720; // The Cost tables, with few columns, from the rail width.
export const TABLE_MIN = 1080; // The Forage tables (and the Devices list) from the wide rail.
export const WIDE_TABLE_MIN = 1280; // The Fleet and the Capping queue, with the most columns.

const query = (px: number): MediaQueryList | null =>
  typeof window.matchMedia === 'function' ? window.matchMedia(`(min-width: ${px}px)`) : null;

export function useMinWidth(px: number): boolean {
  const subscribe = useCallback(
    (listener: () => void): (() => void) => {
      const list = query(px);
      if (list === null) return () => undefined;
      list.addEventListener('change', listener);
      return () => list.removeEventListener('change', listener);
    },
    [px],
  );
  // No media queries at all (tests) reads as reached, the layout that holds every control.
  const snapshot = useCallback((): boolean => query(px)?.matches ?? true, [px]);
  return useSyncExternalStore(subscribe, snapshot, () => true);
}
