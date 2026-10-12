/**
 * The Fleet view's face: its route component and its slice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Imported by `src/shell/outlet.tsx` (the view)
 *   and `src/app.tsx` (the slice, for the table of slices).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 5.1.
 */
export { FleetView } from './fleet';
export { fleetSlice } from './slice';
