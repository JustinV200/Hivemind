/**
 * `useBurstRead()`: read again once, a second after the first security event of a burst.
 *
 * The Entrance's security stream says something changed; it does not carry the new list. The first
 * frame of a burst opens a one second window and one read closes it, so a busy door (a lockout
 * burst, a run of enrolments) is read at most once a second and a steady stream cannot starve the
 * read. With no frame there is no window and no read: the screen never polls.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/live/`. Used by `use_devices.ts` and by the Enrol
 *   view's hook, which both keep a list in step with `/v1/entrance/stream`.
 *
 * Key invariants:
 *   - After the seed, a read happens only because `ticks` rose above zero.
 *   - Frames inside an open window join it: one window, one read.
 *   - Leaving the view cancels a window that is still open.
 *
 * See Also:
 *   - src/views/capping/use_capping.ts, the same pattern for the trail stream.
 */
import { useEffect, useRef } from 'react';

export const SECURITY_STREAM = '/v1/entrance/stream';

// The server writes the trail before it pushes, so a burst settles in about a second.
export const REREAD_DELAY_MS = 1000;

export function useBurstRead(ticks: number, read: () => void): void {
  const timer = useRef<number | null>(null);

  // The first frame of a burst opens a window; later ones find it open and do nothing.
  useEffect(() => {
    if (ticks === 0 || timer.current !== null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      read();
    }, REREAD_DELAY_MS);
  }, [ticks, read]);

  // Leaving cancels a window that is still open.
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
    },
    [],
  );
}
