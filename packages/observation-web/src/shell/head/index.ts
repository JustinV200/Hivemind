/**
 * The head package's face: the sticky bar, banner, strip and notice bar above every view.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/shell/head/`. Imported by `src/shell/shell.tsx` and, for the
 *   device line and log out that move into the More panel on a phone, by `src/shell/nav.tsx`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - docs/observation/design.md section 2.
 */
export { NoticeBar } from './notice_bar';
export { StepUpBanner } from './step_up_banner';
export { SummaryStrip } from './summary_strip';
export { DeviceLine, TopBar, useLogOut } from './top_bar';
