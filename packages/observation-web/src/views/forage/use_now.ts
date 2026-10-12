/**
 * `useNow()`: the current time as state, refreshed on a slow tick, for lease countdowns.
 *
 * "Nearing expiry" depends on the clock as well as the ledger, and a pure diagram cannot read the
 * clock itself. The view reads it once here and passes it down, so every component of one render
 * agrees on what "now" is and a test can hand in any time it likes. The tick only re-renders; it
 * never reads from the network.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/`. Used by `forage_screen.tsx`.
 *
 * Key invariants:
 *   - The interval is cleared on unmount.
 *   - No request is made from here.
 *
 * See Also:
 *   - src/views/forage/tables/expiry.ts for the rule that uses the time.
 */
import { useEffect, useState } from 'react';

export const TICK_MS = 30_000; // Leases run for minutes, so a half-minute tick is fine enough.

export function useNow(intervalMs: number = TICK_MS): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // Re-render on each tick so the countdown text and the ten-minute highlight keep moving.
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
