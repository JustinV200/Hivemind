/**
 * The time package's face: the relative time stamp, the clock it ticks on and the clock words.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/time/`. Re-exported by `src/components/index.ts`;
 *   `relativeText` is exported for tests and for views that need the words without the element,
 *   `clockWords` for the two `stale since` notes (the strip's and the title row's).
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3.
 */
export { useNowMs } from './clock';
export { clockWords, relativeText, TimeStamp } from './time_stamp';
