/**
 * The Enrol view's face: the route component, the slice `src/app.tsx` registers and the join screen.
 *
 * The view is a package (a slice, an invite panel, a pending list, a hook and a stylesheet), plus
 * the join screen a new device lands on, which lives in `join/` because the login screen shows it
 * before any session exists. Importers take everything from here.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/`. Imported by `src/shell/outlet.tsx` (the screen),
 *   `src/app.tsx` (the slice) and `src/views/login/` (the join screen and the invite reader).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/enrol/README.md for the view's files.
 */
export { EnrolScreen } from './enrol_screen';
export { enrolSlice } from './slice';
export type { EnrolEvent, EnrolState } from './slice';
export { inviteCodeFrom, JoinScreen } from './join';
