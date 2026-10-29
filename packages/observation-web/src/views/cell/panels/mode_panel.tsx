/**
 * `ModePanel`: the Cell's access level, its mode, and the last Patrol.
 *
 * Access level says how much of the Cell the Hive may touch. Mode is `ACTIVE` (the Warden is
 * working) or `WATCH` (it is watching), from the Warden's newest mode edge, and is null before the
 * Warden has reported one. The last Patrol is a time the schema fills only once Patrols exist
 * (roadmap phase 11); until then it reads "not tracked yet" rather than disappearing.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Rendered by `cell.tsx` from the page.
 *
 * Key invariants:
 *   - Every row is always present; an unknown value is said in words.
 *   - The page carries only the Patrol's time, not its report, so only the time is shown.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.4 and 5.2.
 */
import type { ReactElement } from 'react';
import { plainCode, StatusDot, TimeStamp } from '../../../components';
import type { CellPage } from '../slice';
import { NotTracked, Panel } from './panel';

export function ModePanel(props: { page: CellPage }): ReactElement {
  const { cell, last_patrol_at: patrol } = props.page;
  return (
    <Panel id="mode" title="Access and mode">
      <dl className="cell-facts">
        <dt>Access level</dt>
        {/* The same words as the header and the Fleet row (`read only`), the code on title. */}
        <dd title={cell.access_level}>{plainCode(cell.access_level)}</dd>
        <dt>Mode</dt>
        <dd>
          {cell.mode === null ? (
            <span className="cell-muted">not reported yet</span>
          ) : (
            <StatusDot word={cell.mode === 'ACTIVE' ? 'active' : 'watch'} />
          )}
        </dd>
        <dt>Last Patrol</dt>
        <dd>{patrol === null ? <NotTracked /> : <TimeStamp at={patrol} />}</dd>
      </dl>
    </Panel>
  );
}
