/**
 * Sorting the Fleet table: the sortable columns, what each sorts by, and the stable order.
 *
 * A header click sorts by that column, ascending, then descending on the next click. Ties break on
 * the Cell id, which is unique, so the order is the same on every draw and a row that changes
 * value moves only as far as its value says. A value the row lacks (no lease, no Warden) always
 * sorts last, in either direction, so the gaps never crowd the top.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/table/`. Used by `fleet_table.tsx` (the headers)
 *   and `fleet.tsx` (the order).
 *
 * Key invariants:
 *   - Pure: the input list is never mutated; no clock and no I/O.
 *   - With no sort chosen the list keeps the aggregate's own order (Wardens' Cells first).
 *   - Enumerations sort by their meaning (open to closed, least to most access), not by spelling.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3.
 */
import type { FleetRow } from '../slice';

export type SortKey =
  | 'id'
  | 'kind'
  | 'source'
  | 'status'
  | 'tier'
  | 'mask'
  | 'access'
  | 'warden'
  | 'models'
  | 'task'
  | 'alarms'
  | 'wax'
  | 'lease';

export interface SortState {
  key: SortKey;
  direction: 'ascending' | 'descending';
}

// The header words, in column order; the row's cells use the same words as their card labels.
export const COLUMNS: readonly { key: SortKey; label: string }[] = [
  { key: 'id', label: 'Cell' },
  { key: 'kind', label: 'Kind' },
  { key: 'source', label: 'Source' },
  { key: 'status', label: 'Status and mode' },
  { key: 'tier', label: 'Comb Shield' },
  { key: 'mask', label: 'Pheromone Mask' },
  { key: 'access', label: 'Access' },
  { key: 'warden', label: 'Warden runs' },
  { key: 'models', label: 'Models from' },
  { key: 'task', label: 'Task and role' },
  { key: 'alarms', label: 'Open Alarms' },
  { key: 'wax', label: 'Cell Wax' },
  { key: 'lease', label: 'Lease age' },
];

// A missing value ranks at `none`: the lowest (mask `OFF` is also no mask) unless told otherwise.
const rank = <T extends string>(order: readonly T[], value: T | null, none = 0): number =>
  value === null ? none : order.indexOf(value);

type Value = string | number | null;

const VALUE: Record<SortKey, (row: FleetRow) => Value> = {
  id: (row) => row.id,
  kind: (row) => row.kind,
  source: (row) => row.source,
  status: (row) => row.status,
  tier: (row) => rank(['MEADOW', 'PROPOLIS', 'NIGHT_VEIL'], row.comb_shield),
  mask: (row) => rank(['OFF', 'WARDEN', 'QUEEN_FORCED'], row.mask_state),
  access: (row) => rank(['READ_ONLY', 'SCRATCH', 'FULL'], row.access_level),
  warden: (row) => row.warden_runs,
  models: (row) => row.models_from,
  task: (row) => row.current_task_id,
  alarms: (row) => row.open_alarms,
  wax: (row) => rank(['NOTE', 'CAUTION', 'BLOCK'], row.highest_wax_severity, -1),
  lease: (row) => row.lease_age_s,
};

const compareValues = (a: string | number, b: string | number): number => {
  if (a === b) return 0;
  return a < b ? -1 : 1;
};

// A click on the sorted column flips it; a click on another column starts that one ascending.
export function nextSort(current: SortState | null, key: SortKey): SortState {
  if (current?.key === key) {
    return { key, direction: current.direction === 'ascending' ? 'descending' : 'ascending' };
  }
  return { key, direction: 'ascending' };
}

export function sortRows(rows: readonly FleetRow[], sort: SortState | null): readonly FleetRow[] {
  if (sort === null) return rows;
  const value = VALUE[sort.key];
  const sign = sort.direction === 'ascending' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = value(a);
    const right = value(b);
    // A missing value is last whichever way the column runs, so only real values are flipped.
    if (left === null || right === null) {
      if (left !== right) return left === null ? 1 : -1;
    } else {
      const order = compareValues(left, right);
      if (order !== 0) return order * sign;
    }
    return compareValues(a.id, b.id);
  });
}
