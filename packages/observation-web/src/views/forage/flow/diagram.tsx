/**
 * `ForageFlow`: the Queen's shared pool flowing to each Warden's grants and each grant's spend.
 *
 * It draws the layout of `layout.ts` as SVG: three columns of boxes joined by ribbons. A box shows
 * how much was issued (its height) and, where the schema carries a use figure, how much of that has
 * been used (the filled part). A grant nearing expiry has its box and ribbon edge in the warn token
 * and the word "soon" in its label. Beside the SVG a table repeats every figure for screen readers.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/flow/`. Drawn by `forage_screen.tsx`; makes no
 *   request and holds no state.
 *
 * Key invariants:
 *   - A pure function of its model: no timers, no random numbers; two renders are identical.
 *   - Every box and ribbon has a `<title>`; fills and strokes are `var(--...)` tokens only.
 *   - Text is real `<text>`, cut to fit; the full text is in `<title>`.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.7 and 4.8.
 */
import type { ReactElement } from 'react';
import { formatAmount } from './model';
import type { FlowModel } from './model';
import { layoutFlow } from './layout';
import type { FlowBox, FlowRibbon, NodeKind } from './layout';

// The pool's reserve is hatched; granted-side boxes are the granted token; free is the track.
const FILL: Readonly<Record<NodeKind, string>> = {
  reserve: 'url(#forage-flow-hatch)',
  granted: 'var(--ok)',
  free: 'var(--border-soft)',
  warden: 'var(--ok)',
  grant: 'var(--ok)',
};

function Box(props: { box: FlowBox }): ReactElement {
  const { box } = props;
  return (
    <g>
      <title>{box.title}</title>
      <rect
        x={box.x}
        y={box.y}
        width={box.w}
        height={box.h}
        fill={FILL[box.kind]}
        stroke={box.near ? 'var(--warn)' : 'var(--border)'}
        strokeWidth={box.near ? 3 : 1}
      />
      {box.usedH > 0 ? (
        <rect
          x={box.x}
          y={box.y + box.h - box.usedH}
          width={box.w}
          height={box.usedH}
          fill="var(--accent)"
        />
      ) : null}
      <text className="forage-svg-label" x={box.x + box.w + 4} y={box.y + box.h / 2 + 4}>
        {box.label}
      </text>
    </g>
  );
}

// The hatch for the Royal Reserve, so it differs from the other boxes by shape and not colour alone.
function HatchDefs(): ReactElement {
  return (
    <defs>
      <pattern
        id="forage-flow-hatch"
        width="6"
        height="6"
        patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"
      >
        <rect width="6" height="6" fill="var(--surface-2)" />
        <line x1="0" y1="0" x2="0" y2="6" stroke="var(--idle)" strokeWidth="3" />
      </pattern>
    </defs>
  );
}

function Ribbon(props: { ribbon: FlowRibbon }): ReactElement {
  const { ribbon } = props;
  return (
    <path
      d={ribbon.path}
      fill="var(--ok)"
      fillOpacity="0.25"
      stroke={ribbon.near ? 'var(--warn)' : 'none'}
      strokeWidth="2"
    />
  );
}

export function ForageFlow(props: { model: FlowModel }): ReactElement {
  const { model } = props;
  const layout = layoutFlow(model);
  const empty = layout.boxes.length === 0;
  return (
    <figure className="forage-figure">
      <svg
        className="forage-svg"
        viewBox={`0 0 ${layout.width} ${Math.max(layout.height, 1)}`}
        role="img"
        aria-labelledby="forage-flow-title"
      >
        <title id="forage-flow-title">
          {`Forage flow: the shared pool, to ${model.wardens.length} Wardens, to their grants`}
        </title>
        <HatchDefs />
        {layout.ribbons.map((ribbon) => (
          <Ribbon key={ribbon.id} ribbon={ribbon} />
        ))}
        {layout.boxes.map((box) => (
          <Box key={`${box.column}:${box.id}`} box={box} />
        ))}
      </svg>
      {empty ? <p className="forage-note">Nothing is granted in this unit yet.</p> : null}
      <div className="sr-only">
        <table>
          <caption>Forage flow, in numbers</caption>
          <tbody>
            {layout.boxes.map((box) => (
              <tr key={`${box.column}:${box.id}`}>
                <th scope="row">{box.title}</th>
                <td>{formatAmount(model.unit, box.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
