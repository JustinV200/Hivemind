/**
 * `useGraphMode()`: wide (left to right) or narrow (top to bottom) by the container's width.
 *
 * The graph's layout depends on the room it has, which only the browser knows, so a
 * `ResizeObserver` on the container picks the mode. Before the first measurement, and wherever
 * there is no `ResizeObserver` (the test DOM), the mode is narrow, the phone layout.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/graph/`. Used by `task_graph.tsx`.
 *
 * Key invariants:
 *   - The observer is disconnected when the graph unmounts.
 *   - The mode changes only across `WIDE_FROM`, so a drag of the window does not redraw the
 *     graph on every pixel.
 *
 * See Also:
 *   - docs/observation/design.md section 2, for the breakpoints.
 */
import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { GraphMode } from './layout';

// The narrowest container that gets the left to right layout.
export const WIDE_FROM = 720;

export function useGraphMode(): [RefObject<HTMLDivElement | null>, GraphMode] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [mode, setMode] = useState<GraphMode>('narrow');
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
