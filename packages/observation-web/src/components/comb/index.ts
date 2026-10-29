/**
 * The comb package's face: the places the honeycomb is drawn as a shape.
 *
 * The brand mark (alone or in the brand header), the faint comb pattern and the empty state that
 * sits on it travel together, so the motif stays in the few places design.md allows it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/comb/`. Re-exported by `src/components/index.ts`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 3.4.
 */
export { BrandHeader } from './brand_header';
export { EmptyState } from './empty_state';
export { HexPattern } from './hex_pattern';
export { HEXAGON, Mark } from './mark';
