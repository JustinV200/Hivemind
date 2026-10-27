/**
 * `TaskNodeView`: one task in the graph, as a comb cell with its id, status, placement and Alarms.
 *
 * Three lines of 12 unit text sit in a fixed box drawn as a wide flat-top hexagon, the same
 * shape the Cell diagram gives a bee: the task id, the status word beside a dot in the status
 * colour, and the placement Cell. An Alarm count badge sits in the top right corner when the task
 * has open Alarms. The whole box is a link when the task names a bee or a Cell, and a plain group
 * when it is not placed yet. A box too long for its line is cut and the `<title>` keeps the full
 * text.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/graph/`. Drawn by `task_graph.tsx` at the
 *   position `layout.ts` gives it; the shape comes from `src/components/comb/shapes.ts`.
 *
 * Key invariants:
 *   - Only tokens are painted (`var(--ok)`), so both themes work.
 *   - The Alarm badge says the count in text, and the outline turns to the Alarm token with it, so
 *     it is not told by colour alone.
 *   - Text starts past the slanted left side, so nothing crosses the outline.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.4, 4.4 and 4.8.
 */
import type { ReactElement, ReactNode } from 'react';
import { hexCardPath } from '../../../components/comb/shapes';
import type { PlacedNode } from './layout';
import { describeNode, hrefOfNode, paintOf, placementOf, statusWord, toneOf } from './status';
import type { TaskNode } from './status';

// Characters a line keeps before the cut; the box is 164 units and mono 12 unit text is ~7 wide.
const ID_CUT = 19;
const CELL_CUT = 22;
const NODE_CUT = 8; // The hexagon's points stand this far in from the box's sides.
const INSET = 12; // Where the text starts, inside the slanted left edge.

const cut = (text: string, keep: number): string =>
  text.length > keep ? `${text.slice(0, keep - 1)}…` : text;

function Frame(props: { node: TaskNode; href: string | null; children: ReactNode }): ReactElement {
  const label = describeNode(props.node);
  if (props.href === null) {
    return (
      <g aria-label={label} role="group" data-task={props.node.task_id} className="ag-node">
        <title>{label}</title>
        {props.children}
      </g>
    );
  }
  return (
    <a href={props.href} aria-label={label} data-task={props.node.task_id} className="ag-node">
      <title>{label}</title>
      {props.children}
    </a>
  );
}

export function TaskNodeView(props: { placed: PlacedNode; node: TaskNode }): ReactElement {
  const { placed, node } = props;
  const { x, y, w } = placed;
  const alarmed = node.open_alarms > 0;
  const tone = paintOf(toneOf(node.status));
  return (
    <Frame node={node} href={hrefOfNode(node)}>
      <path
        className="ag-shape"
        d={hexCardPath(placed, NODE_CUT)}
        fill="var(--surface)"
        stroke={alarmed ? 'var(--alarm)' : tone}
        strokeWidth={2}
      />
      <text className="ag-id" x={x + INSET} y={y + 18}>
        {cut(node.task_id, ID_CUT)}
      </text>
      <circle cx={x + INSET + 5} cy={y + 32} r={5} fill={tone} />
      <text className="ag-word" x={x + INSET + 15} y={y + 36}>
        {statusWord(node.status)}
      </text>
      <text className="ag-cell" x={x + INSET} y={y + 52}>
        {cut(placementOf(node), CELL_CUT)}
      </text>
      {alarmed ? (
        <g className="ag-alarm" data-alarms={node.open_alarms}>
          <rect x={x + w - 46} y={y + 7} width={34} height={18} rx={9} fill="var(--alarm)" />
          <text x={x + w - 29} y={y + 20} textAnchor="middle">
            {node.open_alarms}
          </text>
        </g>
      ) : null}
    </Frame>
  );
}
