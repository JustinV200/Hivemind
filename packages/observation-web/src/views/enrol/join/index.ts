/**
 * The join package's face: the join screen and the reader of the invite in the page's address.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/join/`. Re-exported by `src/views/enrol/index.ts`
 *   and imported by the login screen.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/enrol/join/join_screen.tsx for the screen.
 */
export { inviteCodeFrom } from './invite_code';
export { JoinScreen } from './join_screen';
