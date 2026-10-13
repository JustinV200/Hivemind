/**
 * `useKindFilter()`: the Real / Virtual / All choice, remembered for this viewer.
 *
 * The choice is a convenience, not state anyone else needs, so it lives in `localStorage` under
 * one key. Storage can be missing or throw (a private window, blocked site data), so every read
 * and write is wrapped and the filter then simply starts at All and forgets on reload.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/filter/`. Used by `fleet.tsx`.
 *
 * Key invariants:
 *   - Never throws; an unreadable, unknown or missing stored value reads as `ALL`.
 *   - Writes one key, `hivemind.fleet.kind`, and nothing else.
 *
 * See Also:
 *   - docs/observation/design.md section 5.1.
 */
import { useCallback, useState } from 'react';
import type { FleetRow } from '../slice';

export type KindChoice = 'ALL' | 'REAL' | 'VIRTUAL';

export const KIND_KEY = 'hivemind.fleet.kind';

function stored(): KindChoice {
  try {
    const value = window.localStorage.getItem(KIND_KEY);
    if (value === 'REAL' || value === 'VIRTUAL') return value;
  } catch {
    // Storage is blocked or unavailable here; the filter works without it.
  }
  return 'ALL';
}

function remember(kind: KindChoice): void {
  try {
    window.localStorage.setItem(KIND_KEY, kind);
  } catch {
    // Not remembering is acceptable; the choice still holds until reload.
  }
}

export function useKindFilter(): [KindChoice, (kind: KindChoice) => void] {
  const [kind, setKind] = useState<KindChoice>(stored);
  const choose = useCallback((next: KindChoice) => {
    setKind(next);
    remember(next);
  }, []);
  return [kind, choose];
}

export function byKind(rows: readonly FleetRow[], kind: KindChoice): readonly FleetRow[] {
  return kind === 'ALL' ? rows : rows.filter((row) => row.kind === kind);
}
