/**
 * `TaskNodeView`: one task in the graph, as a comb cell with its id, status, placement and Alarms.
 *
 * Three lines of 12 unit text sit in a fixed box drawn as a wide flat-top hexagon, the same
 * shape the Cell diagram gives a bee: the task id (cut as the tables cut one, `task_…2C3D4`), the
 * status word beside a dot in the status colour, and the placement Cell. A task with open Alarms
 * has its outline in the Alarm tone and the count with its word (`2 Alarms`) at the right of its
 * status line, where the Cell diagram's bee node writes `Alarm`; a bare number told nobody what
 * it counted. The whole box is a link when the task names a bee or a Cell, and a plain group
 * when it is not placed yet. A Cell line too long for the box is cut and the `<title>` keeps the
 * full text.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/graph/`. Drawn by `task_graph.tsx` at the
 *   position `layout.ts` gives it; the shape comes from `src/components/comb/shapes.ts`.
 *
 * Key invariants:
 *   - Only tokens are painted (`var(--ok)`), so both themes work.
 *   - The Alarms are said in words with their count, and the outline turns to the Alarm token with
 *     them, so they are not told by colour alone.
 *   - Text starts past the slanted left side, so nothing crosses the outline; the id is at most
 *     fourteen characters (`idText`), so it never reaches the box's right side.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.4, 4.3, 4.4 and 4.8.
 */
import type { ReactElement, ReactNode } from 'react';
import { idText } from '../../../components';
import { hexCardPath } from '../../../components/comb/shapes';
import type { PlacedNode } from './layout';
import { describeNode, hrefOfNode, paintOf, placementOf, statusWord, toneOf } from './status';
import type { TaskNode } from './status';

// Characters the Cell line keeps before the cut; the box is 164 units and 12 unit text is ~6 wide.
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
        {idText(node.task_id)}
      </text>
      <circle cx={x + INSET + 5} cy={y + 32} r={5} fill={tone} />
      <text className="ag-word" x={x + INSET + 15} y={y + 36}>
        {statusWord(node.status)}
      </text>
      {alarmed ? (
        <text
          className="ag-alarm"
          data-alarms={node.open_alarms}
          x={x + w - INSET}
          y={y + 36}
          textAnchor="end"
        >
          {`${node.open_alarms} Alarm${node.open_alarms === 1 ? '' : 's'}`}
        </text>
      ) : null}
      <text className="ag-cell" x={x + INSET} y={y + 52}>
        {cut(placementOf(node), CELL_CUT)}
      </text>
    </Frame>
  );
}
