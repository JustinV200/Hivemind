/**
 * The badges' face: the tier pill and the mask tag every Cell row and Cell page header carries.
 *
 * `CombShieldLevel` (how sealed a Cell is) is a rounded pill and the Pheromone Mask state (whether
 * a bee is hiding that it is a bee) is a square tag, so the two are told apart by shape and words
 * as well as colour (design.md section 3.3). They live together because they are drawn together,
 * first in every row and in every header.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/badges/`. Re-exported by `src/components/index.ts`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.3 and 4.1.
 */
export { MaskBadge } from './mask_badge';
export type { MaskState } from './mask_badge';
export { TierBadge } from './tier_badge';
export type { CombShieldLevel } from './tier_badge';
