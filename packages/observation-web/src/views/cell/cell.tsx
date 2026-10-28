/**
 * The Cell page: one Cell, live, at `#/cells/<id>`.
 *
 * The page is the header (id, kind, source, tier and mask badges, access level and mode), the live
 * diagram of the Cell across the full width (its Warden, sub-bees, session, Exoskeleton, lease or
 * Virtual Cell status and model hosting), and under it the panels in a grid of as many columns as
 * fit: tasks and goals, Forage, mode and Patrol, Cell Wax, open Alarms, Capping and Honey. It is
 * seeded by one read of the Cell's page and redrawn from the Cells and telemetry streams as frames
 * arrive; a trail event naming the Cell re-reads the page once for the lists that have no stream.
 * A refusal or an unknown Cell shows the error table's sentence in place of the page.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/`. Chosen by the outlet in `src/shell/` when the
 *   route is `cell`. Built to docs/observation/design.md section 5.2.
 *
 * Key invariants:
 *   - Read-only: it reads the page and three streams, and writes only its own slice.
 *   - Everything drawn comes from the generated types; no field is guessed, and a field the schema
 *     documents as filled in a later phase says "not tracked yet".
 *   - A page left over from another Cell is never shown: it must match the route's id.
 *
 * See Also:
 *   - src/views/cell/README.md.
 *   - src/views/cell/slice.ts and src/views/cell/use_cell.ts for how the page stays live.
 */
import { useMemo } from 'react';
import type { ReactElement } from 'react';
import { ErrorNotice } from '../../components';
import { useSlice } from '../../store';
import './cell.css';
import { awakeSet, CellDiagram } from './diagram';
import { CellHeader } from './header';
import {
  AlarmsPanel,
  CappingPanel,
  ForagePanel,
  HoneyPanel,
  ModePanel,
  TasksPanel,
  WaxPanel,
} from './panels';
import { useCellPage } from './use_cell';

export function CellPageView(props: { id: string }): ReactElement {
  const state = useCellPage(props.id);
  const summary = useSlice('summary');
  // Bees in an awake episode, from the summary slice, for the diagram's accent outline.
  const awake = useMemo(() => awakeSet(summary.autopilot), [summary.autopilot]);
  // A render between a route change and the slice opening the new Cell must not show the old one.
  const mine = state.cellId === props.id;
  const page = mine ? state.page : null;
  const error = mine ? state.error : null;
  return (
    <>
      <CellHeader id={props.id} page={page} />
      {error ? <ErrorNotice error={error} /> : null}
      {!page && !error ? <p aria-busy="true">Reading the Cell</p> : null}
      {page ? (
        <div className="cell-page">
          <CellDiagram page={page} awake={awake} />
          <div className="cell-panels">
            <TasksPanel page={page} />
            <ForagePanel page={page} />
            <ModePanel page={page} />
            <WaxPanel page={page} />
            <AlarmsPanel page={page} />
            <CappingPanel page={page} />
            <HoneyPanel page={page} />
          </div>
        </div>
      ) : null}
    </>
  );
}
