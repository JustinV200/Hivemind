/**
 * Folding one Cell's own fields into the Fleet's rows.
 *
 * A `CellFrame` carries a Cell (kind, status, mode, tier, mask, access level, lease and Virtual
 * status, Warden). The aggregate's row carries those too, so the row can be brought up to date at
 * once, without waiting for the next read of the aggregate. Everything the Cell does not know
 * (where the Warden runs, the models' source, the task's role, Alarms, Cell Wax, the lease's age
 * while it stays open) is left as the aggregate gave it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Called by the reducer in `slice.ts`.
 *
 * Key invariants:
 *   - Pure: no clock, no I/O.
 *   - A row whose folded fields are all unchanged keeps its identity, and so does the list when
 *     nothing in it changed.
 *   - A Cell the list has not seen is added with plain defaults (zero Alarms, no Cell Wax, models
 *     `unknown`) until the aggregate fills them in; the list never grows past `MAX_ROWS`.
 *
 * See Also:
 *   - src/views/fleet/slice.ts, the only caller.
 */
import type { components } from '../../landing_board/types';
import type { FleetRow } from './slice';

type CellView = components['schemas']['CellView'];

// Lives here, not in the slice, so the two files do not import each other's values.
export const MAX_ROWS = 2000;

// Headline words the Cell itself can produce: a lifecycle status, a lease state, or nothing known.
// A status that is none of these came from the Warden's state, which only the aggregate knows.
const CELL_STATUS_WORDS: ReadonlySet<string> = new Set([
  'UNKNOWN',
  'REQUESTED',
  'OPEN',
  'RELEASING',
  'RELEASED',
  'ORPHANED',
  'PROVISIONING',
  'READY',
  'GRANTED',
  'DORMANT',
  'DESTROYING',
  'DESTROYED',
  'FAILED',
]);

// The aggregate's headline rule, as far as one Cell can follow it: a Virtual status wins, then the
// lease state; a Warden-derived word stays until the aggregate says otherwise.
function headline(previous: string, cell: CellView): string {
  if (cell.virtual_status) return cell.virtual_status;
  if (cell.lease_state && CELL_STATUS_WORDS.has(previous)) return cell.lease_state;
  return previous;
}

function rowOf(cell: CellView): FleetRow {
  return {
    access_level: cell.access_level,
    comb_shield: cell.comb_shield,
    current_task_id: cell.current_tasks[0] ?? null,
    current_task_role: null,
    highest_wax_severity: null,
    id: cell.id,
    kind: cell.kind,
    lease_age_s: null,
    links: {
      attendant_principal: cell.warden_id,
      cell_id: cell.id,
      thought_principals: cell.warden_id ? [cell.warden_id] : [],
    },
    mask_state: cell.mask_state ?? null,
    mode: cell.mode,
    models_from: 'unknown',
    name: cell.name,
    open_alarms: 0,
    source: cell.source,
    status: headline('UNKNOWN', cell),
    warden_id: cell.warden_id,
    warden_runs: null,
  };
}

function merged(row: FleetRow, cell: CellView): FleetRow {
  const next: FleetRow = {
    ...row,
    access_level: cell.access_level,
    comb_shield: cell.comb_shield,
    kind: cell.kind,
    // An age only means something while the lease is open; once it is not, there is none.
    lease_age_s: cell.lease_state === 'OPEN' ? row.lease_age_s : null,
    mask_state: cell.mask_state ?? null,
    mode: cell.mode,
    name: cell.name,
    source: cell.source,
    status: headline(row.status, cell),
    warden_id: cell.warden_id,
  };
  const keys = Object.keys(next) as (keyof FleetRow)[];
  return keys.every((key) => next[key] === row[key]) ? row : next;
}

export function foldCell(rows: readonly FleetRow[], cell: CellView): readonly FleetRow[] {
  const at = rows.findIndex((row) => row.id === cell.id);
  if (at < 0) return rows.length < MAX_ROWS ? [...rows, rowOf(cell)] : rows;
  const before = rows[at]!;
  const after = merged(before, cell);
  return after === before ? rows : rows.map((row, i) => (i === at ? after : row));
}
