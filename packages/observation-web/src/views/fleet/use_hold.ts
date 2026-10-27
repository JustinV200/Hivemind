/**
 * `useHold(shown)`: keep the rows still while the reader is in the table, and count what waits.
 *
 * Design 4.3: while focus or the pointer is inside the table, new rows wait behind a "N new
 * rows, re-sort" button instead of moving the list, so a row is never pulled out from under a
 * click. Held, the list keeps the order it had when the reader entered (a row that changes value
 * still updates in place; a row that vanished is dropped), and the rows that arrived since are
 * counted. The button, or leaving the table, lets the current order through.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Used by `fleet.tsx`; the handlers go on the
 *   table's scroll box.
 *
 * Key invariants:
 *   - The hold never hides an update to a row already shown: only new rows and re-ordering wait.
 *   - Changing the sort or the filter releases the hold, since the reader asked for a new order.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3 (tables).
 */
import { useState } from 'react';
import type { FleetRow } from './slice';

export interface Hold {
  rows: readonly FleetRow[];
  // How many rows arrived while held, and so are not yet drawn.
  fresh: number;
  hold: () => void;
  release: () => void;
  // Lets the waiting rows through and holds again from the new order (the button's action).
  refresh: () => void;
}

export function useHold(shown: readonly FleetRow[]): Hold {
  // The ids in the order the reader saw when the hold began; null when nothing is held.
  const [frozen, setFrozen] = useState<readonly string[] | null>(null);
  const ids = (): readonly string[] => shown.map((row) => row.id);
  if (frozen === null) {
    return {
      rows: shown,
      fresh: 0,
      hold: () => setFrozen(ids()),
      release: () => undefined,
      refresh: () => setFrozen(ids()),
    };
  }
  // The held order, with each row's current values, and without rows that have gone.
  const byId = new Map(shown.map((row) => [row.id, row]));
  const rows = frozen.flatMap((id) => {
    const row = byId.get(id);
    return row ? [row] : [];
  });
  const held = new Set(frozen);
  return {
    rows,
    fresh: shown.filter((row) => !held.has(row.id)).length,
    hold: () => undefined,
    release: () => setFrozen(null),
    refresh: () => setFrozen(ids()),
  };
}
