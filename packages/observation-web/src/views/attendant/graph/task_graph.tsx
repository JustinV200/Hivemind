/**
 * `TaskGraph`: the principal's tasks as a live layered SVG, with a hidden table behind it.
 *
 * The graph is a pure function of the nodes and edges the slice holds: `layoutGraph` places them,
 * and this file draws the lines (with an arrowhead at the task that waits), then the nodes over
 * them. The `<svg>` is `role="img"` with an `aria-labelledby` title that says how many tasks and
 * dependencies it shows; every node has its own `<title>`. In the wide mode the drawing keeps its
 * natural size inside a container that scrolls sideways, so a long chain never shrinks its text
 * below 12 pixels; in the narrow mode it scales by its viewBox, which is 360 units across.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/graph/`. Drawn by `attendant.tsx`.
 *
 * Key invariants:
 *   - No timers and no randomness: the same slice gives the same markup.
 *   - Only tokens are painted; text is real `<text>`.
 *   - A node with no entry in `nodes` is never drawn: the layout works from the node ids.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.8 and 5.5.
 */
import { useMemo } from 'react';
import type { ReactElement } from 'react';
import { GraphTable } from './graph_table';
import { layoutGraph } from './layout';
import type { GraphEdge } from './layout';
import { TaskNodeView } from './task_node';
import type { TaskNode } from './status';
import { useGraphMode } from './use_graph_mode';

const HEADING_ID = 'attendant-graph-title';
const ARROW_ID = 'attendant-graph-arrow';

function summaryOf(principal: string, tasks: number, edges: number): string {
  const t = `${tasks} task${tasks === 1 ? '' : 's'}`;
  const d = `${edges} dependenc${edges === 1 ? 'y' : 'ies'}`;
  return `Task graph of ${principal}: ${t}, ${d}. A line runs from a task to the task that waits for it.`;
}

export function TaskGraph(props: {
  principal: string;
  nodes: readonly TaskNode[];
  edges: readonly GraphEdge[];
}): ReactElement {
  const { principal, nodes, edges } = props;
  const [ref, mode] = useGraphMode();
  const layout = useMemo(
    () =>
      layoutGraph(
        nodes.map((node) => node.task_id),
        edges,
        mode,
      ),
    [nodes, edges, mode],
  );
  const byId = useMemo(() => new Map(nodes.map((node) => [node.task_id, node])), [nodes]);
  const summary = summaryOf(principal, nodes.length, layout.lines.length);
  return (
    <div className={`ag ag--${mode}`} ref={ref}>
      <svg
        className="ag-svg"
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width={mode === 'wide' ? layout.width : undefined}
        role="img"
        aria-labelledby={HEADING_ID}
        aria-label={summary}
      >
        <title id={HEADING_ID}>{summary}</title>
        <defs>
          <marker
            id={ARROW_ID}
            viewBox="0 0 8 8"
            refX={7}
            refY={4}
            markerWidth={8}
            markerHeight={8}
            orient="auto"
          >
            <path d="M0 0 L8 4 L0 8 Z" fill="var(--border)" />
          </marker>
        </defs>
        {layout.lines.map((line) => (
          <path
            key={line.key}
            d={line.d}
            fill="none"
            stroke="var(--border)"
            strokeWidth={1.5}
            markerEnd={`url(#${ARROW_ID})`}
          />
        ))}
        {layout.nodes.map((placed) => {
          const node = byId.get(placed.id);
          return node ? <TaskNodeView key={placed.id} placed={placed} node={node} /> : null;
        })}
      </svg>
      <GraphTable principal={principal} nodes={nodes} edges={edges} />
    </div>
  );
}
