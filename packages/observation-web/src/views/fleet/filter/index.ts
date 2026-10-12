/**
 * The Fleet filter's face: the Real / Virtual / All control and its remembered choice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/filter/`. Imported by `src/views/fleet/fleet.tsx`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 5.1.
 */
export { KindFilter } from './kind_filter';
export { byKind, KIND_KEY, useKindFilter } from './use_kind_filter';
export type { KindChoice } from './use_kind_filter';
