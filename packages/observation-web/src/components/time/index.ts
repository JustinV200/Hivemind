/**
 * The time package's face: the relative time stamp and the clock it ticks on.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/time/`. Re-exported by `src/components/index.ts`;
 *   `relativeText` is exported for tests and for views that need the words without the element.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3.
 */
export { useNowMs } from './clock';
export { relativeText, TimeStamp } from './time_stamp';
