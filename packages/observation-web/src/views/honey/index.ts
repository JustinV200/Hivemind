/**
 * The Honey view's face: the screen and its slice.
 *
 * `src/app.tsx` adds `honeySlice` to its table and the outlet draws `HoneyScreen`; both take them
 * from here.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Imported by `src/app.tsx` and `src/shell/`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/views/honey/honey_screen.tsx and src/views/honey/slice/.
 */
export { HoneyScreen } from './honey_screen';
export { honeySlice } from './slice';
export type { HoneyEvent, HoneyState } from './slice';
