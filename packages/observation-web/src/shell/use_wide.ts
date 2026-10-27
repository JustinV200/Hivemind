/**
 * `useWide()`: whether the page is at the rail width (720 px and up) or the phone width below it.
 *
 * Most of the shell's width rules are stylesheet alone, but two things move between elements
 * rather than restyle: the device name and the log out button live in the top bar on a rail and
 * in the nav's More panel on a phone. Rendering both and hiding one would leave two buttons with
 * the same name in the page for a screen reader, so the shell asks this hook which one to draw.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Used by `top_bar.tsx` and `nav.tsx`.
 *
 * Key invariants:
 *   - Where `matchMedia` is missing (jsdom in tests) the page counts as wide, so the bar holds
 *     the controls the tests look for.
 *   - The breakpoint is the stylesheet's 720 px (design.md section 2) and must stay in step.
 *
 * See Also:
 *   - src/styles/shell.css for the rules that move with it.
 */
import { useSyncExternalStore } from 'react';

const QUERY = '(min-width: 720px)';

const query = (): MediaQueryList | null =>
  typeof window.matchMedia === 'function' ? window.matchMedia(QUERY) : null;

function subscribe(listener: () => void): () => void {
  const list = query();
  if (list === null) return () => undefined;
  list.addEventListener('change', listener);
  return () => list.removeEventListener('change', listener);
}

// No media queries at all (tests) reads as wide, the layout that holds every control.
const snapshot = (): boolean => query()?.matches ?? true;

export function useWide(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => true);
}
