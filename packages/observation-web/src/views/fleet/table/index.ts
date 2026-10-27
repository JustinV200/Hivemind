/**
 * The Fleet table's face: the table, its sort choice and the sort itself.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/table/`. Imported by `src/views/fleet/fleet.tsx`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3.
 */
export { FleetTable } from './fleet_table';
export { SortSelect } from './sort_select';
export { nextSort, sortRows } from './sorting';
export type { SortKey, SortState } from './sorting';
