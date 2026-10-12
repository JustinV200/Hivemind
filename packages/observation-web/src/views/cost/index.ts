/**
 * The Cost view's face: the route component and the slice `src/app.tsx` registers.
 *
 * The view is a package (a slice, tables, a hook and a stylesheet), so importers take it from here
 * and the layout inside can change without touching the shell or the app. The route component is
 * `CostScreen`, so it does not clash with the schema's `CostView` type.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/`. Imported by `src/shell/outlet.tsx` (the screen)
 *   and `src/app.tsx` (the slice).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/cost/README.md for the view's files.
 */
export { CostScreen } from './cost_screen';
export { costSlice } from './slice';
export type { CostEvent, CostState } from './slice';
