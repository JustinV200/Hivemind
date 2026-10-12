/**
 * `CapacityDiagram`: per-host stacked bars of what is in use, granted, held in reserve and free.
 *
 * It draws the layout of `layout.ts` as SVG. Each host is a heading and a bar per dimension (cores,
 * memory, GPUs, model seats, sub-bee slots); the shared pool comes first with the Royal Reserve
 * drawn hatched. Colour never works alone: every bar has its figures in words under it, a legend
 * names the four kinds, and a table with the same numbers follows for screen readers. An
 * over-committed bar shows a mark where its total ends and the words "over by".
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/capacity/`. Drawn by `forage_screen.tsx` from the
 *   hosts' blocks; makes no request and holds no state.
 *
 * Key invariants:
 *   - A pure function of its props: no timers, no random numbers; two renders are identical.
 *   - Every bar has a `<title>` with its full text and the `<svg>` has `role="img"` and an
 *     `aria-labelledby`; fills and strokes are `var(--...)` tokens only.
 *   - Text is real `<text>` and is cut when it would overflow; the full text stays in `<title>`.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.7 and 4.8.
 */
import type { ReactElement } from 'react';
import { cut } from '../format';
import { BAR_HEIGHT, BAR_WIDTH, BAR_X, KIND_WORD, layoutCapacity } from './layout';
import type { LaidBlock, LaidRow } from './layout';
import type { HostBlock, SegmentKind } from './model';

const MAX_CHARS = 54; // About 340 units of 12 px text; longer figures are cut.

// Fill per segment kind. The reserve uses the hatch pattern so it differs from the others by shape.
const FILL: Readonly<Record<SegmentKind, string>> = {
  in_use: 'var(--accent)',
  granted: 'var(--ok)',
  reserve: 'url(#forage-hatch)',
  free: 'var(--border-soft)',
};

export function Legend(): ReactElement {
  return (
    <ul className="forage-legend" aria-label="Capacity legend">
      {(['in_use', 'granted', 'reserve', 'free'] as const).map((kind) => (
        <li key={kind}>
          <svg width="14" height="14" aria-hidden="true">
            <rect width="14" height="14" fill={FILL[kind]} stroke="var(--border)" />
          </svg>
          {KIND_WORD[kind]}
        </li>
      ))}
    </ul>
  );
}

function Row(props: { laid: LaidRow }): ReactElement {
  const { laid } = props;
  const text = `${laid.row.label}: ${laid.figures}`;
  const barY = laid.y + 22;
  return (
    <g>
      <title>{text}</title>
      <text className="forage-svg-label" x={BAR_X} y={laid.y + 14}>
        {laid.row.label}
      </text>
      <rect
        x={BAR_X}
        y={barY}
        width={BAR_WIDTH}
        height={BAR_HEIGHT}
        fill="var(--surface-2)"
        stroke="var(--border)"
      />
      {laid.segments.map((s) => (
        <rect key={s.kind} x={s.x} y={barY} width={s.width} height={BAR_HEIGHT} fill={FILL[s.kind]}>
          <title>{`${laid.row.label}, ${KIND_WORD[s.kind]}: ${s.amount}`}</title>
        </rect>
      ))}
      {laid.limitX !== null ? (
        <line
          x1={laid.limitX}
          x2={laid.limitX}
          y1={barY - 3}
          y2={barY + BAR_HEIGHT + 3}
          stroke="var(--alarm)"
          strokeWidth="2"
        />
      ) : null}
      <text className="forage-svg-figures" x={BAR_X} y={barY + BAR_HEIGHT + 14}>
        {cut(laid.figures, MAX_CHARS)}
      </text>
    </g>
  );
}

function Block(props: { block: LaidBlock }): ReactElement {
  const { block } = props;
  return (
    <g>
      <text className="forage-svg-host" x={BAR_X} y={block.y + 14}>
        {cut(block.title, MAX_CHARS)}
        <title>{block.title}</title>
      </text>
      {block.rows.map((laid) => (
        <Row key={laid.row.key} laid={laid} />
      ))}
    </g>
  );
}

export function CapacityDiagram(props: { blocks: readonly HostBlock[] }): ReactElement {
  const layout = layoutCapacity(props.blocks);
  return (
    <figure className="forage-figure">
      <svg
        className="forage-svg"
        viewBox={`0 0 ${layout.width} ${Math.max(layout.height, 1)}`}
        role="img"
        aria-labelledby="forage-capacity-title"
      >
        <title id="forage-capacity-title">
          {`Capacity of ${layout.blocks.length} hosts and pools: in use, granted, reserve and free`}
        </title>
        <defs>
          <pattern
            id="forage-hatch"
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="6" height="6" fill="var(--surface-2)" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--idle)" strokeWidth="3" />
          </pattern>
        </defs>
        {layout.blocks.map((block) => (
          <Block key={block.id} block={block} />
        ))}
      </svg>
      <Legend />
      <table className="sr-only">
        <caption>Capacity per host, in numbers</caption>
        <tbody>
          {layout.blocks.flatMap((block) =>
            block.rows.map((laid) => (
              <tr key={`${block.id}:${laid.row.key}`}>
                <th scope="row">{`${block.title}, ${laid.row.label}`}</th>
                <td>{laid.figures}</td>
              </tr>
            )),
          )}
        </tbody>
      </table>
    </figure>
  );
}
