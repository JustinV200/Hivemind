/**
 * `CellDiagram`: the Cell as live SVG, the Warden and its sub-bees inside a frame with the facts
 * about the Cell beside or under it.
 *
 * It is a pure function of the page: `model.ts` says what each node and box shows, `layout.ts`
 * says where, and this file draws it. The frame is the Cell, a rectangle with its corners cut like
 * a comb cell; the Warden is drawn inside it when it runs on the Cell and above it (on the Hive
 * Stand) otherwise, with a dashed outline while where it runs is not tracked. Lines join the
 * Warden to every sub-bee and each fact box to what it concerns. The SVG scales by its `viewBox`
 * to the width it is given and switches layout (boxes beside the frame or under it) on its
 * container's width.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/diagram/`. Rendered by `cell.tsx` from the slice's
 *   page; redrawn whenever a Cell, telemetry or re-read changes the page.
 *
 * Key invariants:
 *   - Two renders of the same page and mode are byte-identical: no timer, no random number.
 *   - The `<svg>` is an image with a `<title>` and an `aria-label` that state what it shows, each
 *     node and box has its own `<title>`, and a hidden table repeats the numbers.
 *   - All colours are tokens; text is real `<text>`.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.4, 4.5, 4.6 and 4.8.
 */
import { useMemo } from 'react';
import type { ReactElement } from 'react';
import { chamferedPath } from '../../../components/comb/shapes';
import { BeeNodeView } from './bee_node';
import { DiagramTable } from './diagram_table';
import { layoutDiagram } from './layout';
import type { Layout, Placed } from './layout';
import { buildModel, cut } from './model';
import type { DiagramModel, FactBox } from './model';
import type { CellPage } from '../slice';
import { useLayoutMode } from './use_layout_mode';

// Characters a box's value and detail keep; its `<title>` has the rest.
const BOX_CUT = 24;
const FRAME_CUT = 12; // The frame's corner cut; the boxes take a smaller one.
const BOX_CORNER = 6;

function safeId(text: string): string {
  return `cell-diagram-${text.replace(/[^A-Za-z0-9_-]/g, '_')}`;
}

function FactBoxView(props: { placed: Placed<FactBox> }): ReactElement {
  const { x, y, item } = props.placed;
  return (
    <g data-box={item.key}>
      <title>{item.title}</title>
      <path
        d={chamferedPath(props.placed, BOX_CORNER)}
        fill="var(--surface)"
        stroke="var(--border)"
      />
      <text className="dg-box-label" x={x + 8} y={y + 16}>
        {item.label}
      </text>
      <text className="dg-box-value" x={x + 8} y={y + 34}>
        {cut(item.value, BOX_CUT)}
      </text>
      {item.detail ? (
        <text className="dg-line" x={x + 8} y={y + 50}>
          {cut(item.detail, BOX_CUT)}
        </text>
      ) : null}
    </g>
  );
}

// The words inside the frame when there is nothing to draw there.
function emptyNote(model: DiagramModel): string | null {
  if (model.warden === null) return 'No Warden attached';
  return model.bees.length === 0 ? 'No sub-bees right now' : null;
}

// The frame that is the Cell: its outline, its label and, when it is empty, a note saying so.
function FrameView(props: { model: DiagramModel; frame: Layout['frame'] }): ReactElement {
  const { model, frame } = props;
  const note = emptyNote(model);
  return (
    <>
      <path
        className="dg-frame"
        d={chamferedPath(frame, FRAME_CUT)}
        data-y={frame.y}
        fill="var(--surface-2)"
        stroke="var(--border)"
        strokeWidth={2}
      />
      <text className="dg-frame-label" x={frame.x + FRAME_CUT} y={frame.y + 16}>
        {model.frameLabel}
      </text>
      {note ? (
        <text className="dg-note" x={frame.x + FRAME_CUT} y={frame.y + frame.h - 12}>
          {note}
        </text>
      ) : null}
    </>
  );
}

// The Warden and every sub-bee, each a link to where that bee can be read.
function BeeNodes(props: { model: DiagramModel; layout: Layout }): ReactElement {
  const { model, layout } = props;
  return (
    <>
      {layout.warden ? (
        <BeeNodeView
          placed={layout.warden}
          href={`#/attendant/${encodeURIComponent(layout.warden.item.id)}`}
          dashed={model.wardenRuns === null}
        />
      ) : null}
      {layout.bees.map((placed) => (
        <BeeNodeView
          key={placed.item.id}
          placed={placed}
          href={`#/thoughts/${encodeURIComponent(placed.item.id)}`}
          dashed={false}
        />
      ))}
    </>
  );
}

function Drawing(props: { model: DiagramModel; layout: Layout }): ReactElement {
  const { model, layout } = props;
  const heading = safeId(model.cellId);
  return (
    <svg
      className={`dg dg--${layout.mode}`}
      viewBox={`0 0 ${layout.width} ${layout.height}`}
      role="img"
      aria-labelledby={heading}
      aria-label={model.summary}
    >
      <title id={heading}>{model.summary}</title>
      <FrameView model={model} frame={layout.frame} />
      {layout.links.map((link) => (
        <path key={link.key} d={link.d} fill="none" stroke="var(--border)" strokeWidth={1.5} />
      ))}
      <BeeNodes model={model} layout={layout} />
      {layout.boxes.map((placed) => (
        <FactBoxView key={placed.item.key} placed={placed} />
      ))}
    </svg>
  );
}

export function CellDiagram(props: { page: CellPage; awake: ReadonlySet<string> }): ReactElement {
  const [ref, mode] = useLayoutMode();
  const model = useMemo(() => buildModel(props.page, props.awake), [props.page, props.awake]);
  const layout = useMemo(() => layoutDiagram(model, mode), [model, mode]);
  return (
    <div className="cell-diagram" ref={ref}>
      <Drawing model={model} layout={layout} />
      <DiagramTable model={model} />
    </div>
  );
}
