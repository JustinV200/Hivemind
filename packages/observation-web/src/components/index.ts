/**
 * The components' face: the shared pieces every view draws with.
 *
 * Importers take badges, the status dot, the gauge, the error notice and the title row from here.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Imported by `src/shell/` and `src/views/`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 4 for each component.
 */
export { ErrorNotice } from './error_notice';
export { Gauge } from './gauge';
export { MaskBadge } from './mask_badge';
export type { MaskState } from './mask_badge';
export { StatusDot } from './status_dot';
export type { StatusWord } from './status_dot';
export { TierBadge } from './tier_badge';
export type { CombShieldLevel } from './tier_badge';
export { ViewTitle } from './view_title';
