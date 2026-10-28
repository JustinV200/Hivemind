/**
 * `useNowMs()`: the current time, shared by every relative time stamp on the page.
 *
 * A relative time ("5 min ago") goes stale while the page sits open, so the stamps need a clock
 * that ticks. One timer serves them all: the snapshot is the time rounded down to the tick, which
 * every stamp reads through `useSyncExternalStore`, and a single interval wakes them when it
 * moves. A page with a hundred stamps therefore runs one timer, not a hundred, and the timer stops
 * when the last stamp unmounts.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/time/`. Used by `time_stamp.tsx`. The Forage view keeps
 *   its own exact clock (`src/views/forage/use_now.ts`) because its ten-minute expiry rule and its
 *   tests want an unrounded, per-view time.
 *
 * Key invariants:
 *   - The snapshot is stable within a tick, so React never sees two values in one render.
 *   - No subscriber, no timer: the interval is cleared when the subscriber set empties.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3 (times are relative with the ISO in `<time>`).
 */
import { useSyncExternalStore } from 'react';

export const TICK_MS = 30_000; // Relative times read in minutes; a half-minute tick keeps them honest.

const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

// The time rounded down to the tick, so every stamp in one render agrees on "now".
const snapshot = (): number => Math.floor(Date.now() / TICK_MS) * TICK_MS;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // The first subscriber starts the one timer; each tick wakes every stamp at once.
  if (timer === null) {
    timer = setInterval(() => listeners.forEach((wake) => wake()), TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    // The last stamp leaving stops the timer, so an idle page runs nothing.
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export function useNowMs(): number {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
