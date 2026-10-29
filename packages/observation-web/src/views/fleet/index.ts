/**
 * The Fleet view's face: its route component, its slice and the one-off seed other views use.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Imported by `src/shell/outlet.tsx` (the view),
 *   `src/app.tsx` (the slice, for the table of slices) and the Forage and Thoughts views (the
 *   seed, for the Cells' names when the Fleet has not been read yet).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 5.1.
 */
export { FleetView } from './fleet';
export { fleetSlice } from './slice';
export { useFleetSeed } from './use_fleet_seed';
