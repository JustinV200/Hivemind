/**
 * `usePaneWidth()`: the measured width of a diagram's pane, as React state that follows resizes.
 *
 * A diagram is a pure function of its slice and the room it has (design.md 4.8): its viewBox is
 * sized from the pane it is drawn in, so one viewBox unit is one pixel and 12 unit text draws at
 * the 12 px floor on a phone. The pane's width is known only to the browser, so a `ResizeObserver`
 * on the pane reports it. Before the first measurement, and wherever there is no observer (the
 * test DOM), the width is null and a diagram draws its default canvas, the design's 360 px phone.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Used by the Cell diagram, the Attendant graph
 *   and the Forage flow.
 *
 * Key invariants:
 *   - The observer is disconnected on unmount; the state changes only when the rounded width does,
 *     so a drag of the window does not redraw a diagram on every sub-pixel.
 *   - It reads only the pane's content width: no timer, no polling.
 *
 * See Also:
 *   - src/views/cell/diagram/layout.ts, src/views/attendant/graph/layout.ts and
 *     src/views/forage/flow/layout.ts for what each diagram makes of the width.
 */
import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

export function usePaneWidth(): [RefObject<HTMLDivElement | null>, number | null] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect.width ?? 0;
      // A pane that is not laid out yet (display none, a collapsed panel) measures 0: keep the
      // default canvas rather than a diagram with no room.
      setWidth(measured > 0 ? Math.floor(measured) : null);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}
