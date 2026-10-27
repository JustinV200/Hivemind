/**
 * The Attendant view: one supervisor's task graph and ordered inbox, live, at `#/attendant/<who>`.
 *
 * `<who>` is `queen` or a Warden's id. The screen shows the layered graph of the tasks that
 * principal is concerned with (status colour, placement Cell, Alarm count on every node, a link on
 * each node to the bee or Cell behind it), and beside it the ordered inbox, what recently
 * finished and what is up next. It is seeded by one read of `GET /v1/attendant/{principal}`, each
 * `TaskGraphFrame` for the principal moves a node at once, and the same frames make the view read
 * again once a second later for the inbox order and the two lists. A refusal or an unknown
 * principal shows the error table's sentence in place of the view.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/`. Chosen by the outlet in `src/shell/` when
 *   the route is `attendant`. Built to docs/observation/design.md section 5.5; reached from the
 *   fleet row and the Cell diagram.
 *
 * Key invariants:
 *   - Read-only: it reads the view and one stream, and writes only its own slice.
 *   - Everything drawn comes from the generated types; nothing is guessed or sorted again.
 *   - A view left over from another principal is never shown: it must match the route.
 *
 * See Also:
 *   - src/views/attendant/README.md.
 *   - src/views/attendant/slice.ts and use_attendant.ts for how the view stays live.
 */
import { useMemo } from 'react';
import type { ReactElement } from 'react';
import { ErrorNotice } from '../../components';
import './attendant.css';
import { TaskGraph } from './graph';
import type { TaskNode } from './graph';
import { AttendantHeader } from './header';
import { InboxList, TaskList } from './lists';
import { useAttendant } from './use_attendant';

const NO_NODES: readonly TaskNode[] = [];

export function AttendantScreen(props: { principal: string }): ReactElement {
  const state = useAttendant(props.principal);
  // A render between a route change and the slice opening the new principal must not show the old.
  const mine = state.principal === props.principal;
  const view = mine ? state.view : null;
  const error = mine ? state.error : null;
  const nodes = view?.nodes ?? NO_NODES;
  const byId = useMemo(() => new Map(nodes.map((node) => [node.task_id, node])), [nodes]);
  return (
    <>
      <AttendantHeader principal={props.principal} nodes={nodes} />
      {error ? <ErrorNotice error={error} /> : null}
      {!view && !error ? <p aria-busy="true">Reading the Attendant view</p> : null}
      {view ? (
        <div className="att-page">
          <section className="att-panel comb-cell att-graph" aria-labelledby="att-graph-title">
            <h2 id="att-graph-title">Task graph</h2>
            {nodes.length === 0 ? (
              <p className="att-muted">
                No tasks to show. A task appears here when it is submitted, or placed on a Cell.
              </p>
            ) : (
              <TaskGraph principal={view.principal} nodes={nodes} edges={view.edges} />
            )}
          </section>
          <div className="att-side">
            <InboxList inbox={view.inbox} reconstructed={view.inbox_is_reconstructed} />
            <TaskList
              title="Recently finished"
              empty="Nothing has finished recently."
              ids={view.recently_finished}
              nodes={byId}
            />
            <TaskList
              title="Up next"
              empty="Nothing is waiting to start."
              ids={view.up_next}
              nodes={byId}
            />
          </div>
        </div>
      ) : null}
    </>
  );
}
