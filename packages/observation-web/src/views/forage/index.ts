/**
 * The Forage view's face: the route component and the slice `src/app.tsx` registers.
 *
 * The view is a package (a slice, two diagrams, tables, a hook and a stylesheet), so importers take
 * it from here and the layout inside can change without touching the shell or the app. The route
 * component is `ForageScreen`, so it does not clash with the schema's `ForageView` type.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/`. Imported by `src/shell/outlet.tsx` (the
 *   screen) and `src/app.tsx` (the slice).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/forage/README.md for the view's files.
 */
export { ForageScreen } from './forage_screen';
export { forageSlice } from './slice';
export type { ForageEvent, ForageState } from './slice';
