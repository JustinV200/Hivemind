/**
 * The Attendant view's face: the screen component and its slice.
 *
 * `src/app.tsx` adds `attendantSlice` to its table and the outlet draws `AttendantScreen`; both
 * take them from here. The component is named `AttendantScreen` so it never clashes with the
 * schema's `AttendantView` (the answer of `GET /v1/attendant/{principal}`).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/`. Imported by `src/app.tsx` and `src/shell/`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/attendant/attendant.tsx and src/views/attendant/slice.ts.
 */
export { AttendantScreen } from './attendant';
export { attendantSlice } from './slice';
export type { AttendantEvent, AttendantState } from './slice';
