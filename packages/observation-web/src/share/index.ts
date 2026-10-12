/**
 * The phone host's face: what `src/app.tsx` mounts.
 *
 * The share intake, the waiting notification, the push offer and the invite box exist only inside
 * the Android shell (ADR-0047), and are grouped here so the app has one line to add. Everything else
 * in this directory is internal.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Imported by `src/app.tsx`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/share/README.md for the files.
 */
export { PhoneHost } from './phone_host';
