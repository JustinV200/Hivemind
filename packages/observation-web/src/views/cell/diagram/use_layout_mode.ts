/**
 * `useLayoutMode()`: whether the diagram has room for its wide layout, measured on its container.
 *
 * The page puts the diagram beside the panels on a wide screen and above them on a phone, so the
 * room the diagram has is its container's width, not the window's. A `ResizeObserver` on the
 * container picks `wide` from 720 units up, where the 744 unit viewBox still draws its 12 unit
 * text close to 12 pixels, and `narrow` below. Where there is no observer (a test runtime) the
 * layout is `narrow`, the phone layout, which is also what the first paint uses.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/diagram/`. Used by `cell_diagram.tsx`.
 *
 * Key invariants:
 *   - The observer is disconnected on unmount; the state only changes when the mode does.
 *   - It reads only the container's width: no timer, no polling.
 *
 * See Also:
 *   - src/views/cell/diagram/layout.ts for what each mode draws.
 */
import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { LayoutMode } from './layout';

// The narrowest container that gets the wide layout (its viewBox is 744 units across).
export const WIDE_FROM = 720;

export function useLayoutMode(): [RefObject<HTMLDivElement | null>, LayoutMode] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [mode, setMode] = useState<LayoutMode>('narrow');
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setMode(width >= WIDE_FROM ? 'wide' : 'narrow');
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, mode];
}
