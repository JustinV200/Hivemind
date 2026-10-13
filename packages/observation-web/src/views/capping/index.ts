/**
 * The Capping view's face: the route component and the slice `src/app.tsx` registers.
 *
 * The view is a package (a slice, a strip, two tables, filters, a hook and a stylesheet), so
 * importers take it from here and the layout inside can change without touching the shell or the
 * app. The route component is `CappingScreen`, so it does not clash with the schema's `CappingView`
 * (the answer of `GET /v1/capping`).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/`. Imported by `src/shell/outlet.tsx` (the
 *   screen) and `src/app.tsx` (the slice).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/capping/README.md for the view's files.
 */
export { CappingScreen } from './capping_screen';
export { cappingSlice } from './slice';
export type { CappingEvent, CappingState } from './slice';
