/**
 * `useChanged(value)`: true for a few seconds after the value changes, for the strip's underline.
 *
 * Design section 7 says a changed value is marked, with motion or, when motion is reduced, with
 * a static 2 px underline in the accent for five seconds. The strip's numbers change constantly,
 * so the mark is what tells a reader which one just moved. This hook is the timer: it answers
 * true from the render where the value differs from the last one until five seconds later.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/head/`. Used by `summary_strip.tsx` for each group's value.
 *
 * Key invariants:
 *   - The first value never counts as a change, so a fresh page is not underlined all over.
 *   - The timer is cleared on unmount and restarted on every change, so a value that keeps
 *     moving stays marked.
 *
 * See Also:
 *   - docs/observation/design.md section 7 (reduced motion).
 */
import { useEffect, useRef, useState } from 'react';

export const MARK_MS = 5000; // The five seconds the design gives a changed value.

export function useChanged(value: string): boolean {
  const last = useRef(value);
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    // The same value again is not a change, and the first render sets the baseline only.
    if (last.current === value) return;
    last.current = value;
    setChanged(true);
    const timer = setTimeout(() => setChanged(false), MARK_MS);
    return () => clearTimeout(timer);
  }, [value]);
  return changed;
}
