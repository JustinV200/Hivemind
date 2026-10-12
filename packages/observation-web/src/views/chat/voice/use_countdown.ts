/**
 * `useCountdown(total, onExpire)`: a visible countdown in whole seconds that calls back at zero.
 *
 * Push-to-talk shows the seconds left of its cap. This hook owns the interval: `start` resets to
 * `total` and ticks once a second, `stop` clears it, and when the last second passes it stops
 * itself and calls `onExpire` once. The callback is read through a ref, so a new function on a
 * later render is the one called, and the interval never restarts for it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/voice/`. Used by `use_hold.ts`.
 *
 * Key invariants:
 *   - At most one interval runs; `start` clears any earlier one first.
 *   - Unmounting clears the interval.
 *   - Time is counted in ticks, not read from a clock, so the count is exact under fake timers.
 *
 * See Also:
 *   - src/views/chat/voice/use_hold.ts for the recording it caps.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

const TICK_MS = 1000; // The countdown moves a second at a time.

export interface Countdown {
  left: number;
  start(): void;
  stop(): void;
}

export function useCountdown(total: number, onExpire: () => void): Countdown {
  const [left, setLeft] = useState(total);
  const timer = useRef<number | null>(null);
  const expire = useRef(onExpire);
  useEffect(() => {
    expire.current = onExpire;
  }, [onExpire]);

  const stop = useCallback(() => {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
  }, []);

  const start = useCallback(() => {
    stop();
    setLeft(total);
    let ticks = 0;
    timer.current = window.setInterval(() => {
      ticks += 1;
      setLeft(total - ticks);
      // The cap: at zero the interval ends itself and hands over.
      if (ticks >= total) {
        stop();
        expire.current();
      }
    }, TICK_MS);
  }, [total, stop]);

  useEffect(() => stop, [stop]);
  return { left, start, stop };
}
