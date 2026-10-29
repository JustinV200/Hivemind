/**
 * `GraphTable`: the task graph as a table that only a screen reader sees.
 *
 * A diagram's numbers are never only in its picture (design.md 4.8): this table lists every task
 * with its status, role, placement, Warden, open Alarms and the tasks it waits on, in task id
 * order, so the dependency edges the lines draw are said in words too.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/graph/`. Rendered by `task_graph.tsx` right
 *   after the drawing.
 *
 * Key invariants:
 *   - Same data as the drawing, nothing more: a row per node, "waits on" from the edges.
 *   - Hidden with `sr-only`, never `display: none`, so assistive technology still reads it.
 *
 * See Also:
 *   - docs/observation/design.md section 4.8.
 */
import type { ReactElement } from 'react';
import { plainCode } from '../../../components';
import type { GraphEdge } from './layout';
import { placementOf, statusWord } from './status';
import type { TaskNode } from './status';

export function GraphTable(props: {
  principal: string;
  nodes: readonly TaskNode[];
  edges: readonly GraphEdge[];
}): ReactElement {
  const rows = [...props.nodes].sort((a, b) => (a.task_id < b.task_id ? -1 : 1));
  const waitsOn = (id: string): string =>
    props.edges
      .filter((edge) => edge.after === id)
      .map((edge) => edge.before)
      .sort()
      .join(', ') || 'nothing';
  return (
    <div className="sr-only">
      <table>
        <caption>{`Task graph of ${props.principal}`}</caption>
        <thead>
          <tr>
            <th scope="col">Task</th>
            <th scope="col">Status</th>
            <th scope="col">Role</th>
            <th scope="col">Placement</th>
            <th scope="col">Warden</th>
            <th scope="col">Open Alarms</th>
            <th scope="col">Waits on</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((node) => (
            <tr key={node.task_id}>
              <th scope="row">{node.task_id}</th>
              <td>{statusWord(node.status)}</td>
              <td>{plainCode(node.role)}</td>
              <td>{placementOf(node)}</td>
              <td>{node.warden_id ?? 'none'}</td>
              <td>{node.open_alarms}</td>
              <td>{waitsOn(node.task_id)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
