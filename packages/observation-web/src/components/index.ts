/**
 * The components' face: the shared pieces every view draws with.
 *
 * Importers take badges, the status dot, the gauge, the error notice, the view boundary, the
 * title row, the sort control, the empty state, the brand mark and the comb pattern from here.
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
export { MaskBadge, TierBadge } from './badges';
export type { CombShieldLevel, MaskState } from './badges';
export { EmptyState, HexPattern, HEXAGON, Mark } from './comb';
export { ErrorNotice } from './error_notice';
export { Gauge } from './gauge';
export { IdToken } from './id_token';
export { StatusDot } from './status_dot';
export type { StatusWord } from './status_dot';
export { TimeStamp } from './time';
export {
  NARROW_TABLE_MIN,
  RAIL_MIN,
  TABLE_MIN,
  useMinWidth,
  WIDE_TABLE_MIN,
} from './use_min_width';
export { SortSelect } from './sort_select';
export type { SortChoice, SortDirection, SortOption } from './sort_select';
export { ViewBoundary } from './view_boundary';
export { ViewTitle } from './view_title';
