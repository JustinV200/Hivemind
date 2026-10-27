/**
 * The components' face: the shared pieces every view draws with.
 *
 * Importers take badges, the status dot, the gauge, the error notice, the view boundary, the
 * title row, the empty state, the brand mark and the comb pattern from here.
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
export { EmptyState, HexPattern, Mark } from './comb';
export { ErrorNotice } from './error_notice';
export { Gauge } from './gauge';
export { IdToken } from './id_token';
export { MaskBadge } from './mask_badge';
export type { MaskState } from './mask_badge';
export { StatusDot } from './status_dot';
export type { StatusWord } from './status_dot';
export { TimeStamp } from './time';
export { RAIL_MIN, TABLE_MIN, useMinWidth } from './use_min_width';
export { TierBadge } from './tier_badge';
export type { CombShieldLevel } from './tier_badge';
export { ViewBoundary } from './view_boundary';
export { ViewTitle } from './view_title';
