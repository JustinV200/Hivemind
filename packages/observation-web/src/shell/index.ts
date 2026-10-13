/**
 * The shell's face: the one component the app mounts after login.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/`. Imported by `src/app.tsx`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/shell/shell.tsx.
 */
export { Shell } from './shell';
