/**
 * The Fleet view: every Cell in one table, at `#/fleet`.
 *
 * Each row shows the Cell's kind, source, status and mode, its Comb Shield level and Pheromone
 * Mask as badges that are never hidden, its access level, where its Warden runs and where its
 * models come from, its current task and role, open Alarms, the highest live Cell Wax severity,
 * its lease's age and links to its page, its Warden's Attendant view and its bees' thoughts. The
 * legend of the three Comb Shield levels sits in the title row and is never collapsed. A Real /
 * Virtual / All filter (remembered per viewer) and a click-to-sort header (one "Sort by" control
 * on a phone) choose what is drawn, and while the reader is in the table new rows wait behind a
 * button rather than move the list.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Chosen by the outlet in `src/shell/` when the
 *   route is `fleet`. Seeds by `GET /v1/fleet`, stays live on `/v1/cells/stream`.
 *
 * Key invariants:
 *   - Read-only: it reads the aggregate and one stream, and writes only its own slice.
 *   - A refusal (no `observe`) is shown as the error table's sentence, never as an empty table.
 *   - Everything drawn comes from the generated types.
 *
 * See Also:
 *   - docs/observation/design.md section 5.1.
 *   - src/views/fleet/README.md.
 */
import { useMemo, useState } from 'react';
import type { ReactElement } from 'react';
import { EmptyState, ErrorNotice, ViewTitle } from '../../components';
import { useViewStream } from '../../store';
import { byKind, KindFilter, useKindFilter } from './filter';
import './fleet.css';
import { Legend } from './legend';
import { FleetTable, nextSort, sortRows } from './table';
import type { SortState } from './table';
import type { CellFrame, FleetEvent } from './slice';
import { useFleet } from './use_fleet';
import { useHold } from './use_hold';

export const CELLS_STREAM = '/v1/cells/stream';

// Module-level so the stream is opened once per mount, not once per render.
const toEvent = (frame: CellFrame): FleetEvent => ({ type: 'frame', frame });

const EMPTY = 'No Cells yet. A Cell appears here when the Queen provisions or borrows one.';

export function FleetView(props: { rereadMs?: number }): ReactElement {
  const state = useFleet(props.rereadMs);
  useViewStream(CELLS_STREAM, 'fleet', toEvent);
  const [kind, setKind] = useKindFilter();
  const [sort, setSort] = useState<SortState | null>(null);

  // The order is recomputed only when the rows, the filter or the sort change.
  const shown = useMemo(() => sortRows(byKind(state.rows, kind), sort), [state.rows, kind, sort]);
  const hold = useHold(shown);
  // A new order the reader asked for is shown at once, whatever is held.
  const chooseSort = (next: SortState | null): void => {
    hold.release();
    setSort(next);
  };
  return (
    <>
      <ViewTitle title="Fleet">
        <KindFilter
          kind={kind}
          onChoose={(next) => {
            hold.release();
            setKind(next);
          }}
        />
        <Legend entries={state.legend} />
      </ViewTitle>
      {state.error ? <ErrorNotice error={state.error} /> : null}
      {!state.seeded && !state.error ? <p aria-busy="true">Reading the fleet</p> : null}
      {state.seeded && state.rows.length === 0 ? <EmptyState>{EMPTY}</EmptyState> : null}
      {state.rows.length > 0 && shown.length === 0 ? (
        <EmptyState>No {kind === 'REAL' ? 'Real' : 'Virtual'} Cells right now.</EmptyState>
      ) : null}
      {shown.length > 0 ? (
        <FleetTable
          rows={hold.rows}
          sort={sort}
          onSort={(key) => chooseSort(nextSort(sort, key))}
          onSortChange={chooseSort}
          hold={hold}
        />
      ) : null}
    </>
  );
}
