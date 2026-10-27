/**
 * `CapacityDiagram`: one compact card per host (or pool), a bar per dimension with its figures.
 *
 * Each block of `model.ts` becomes a hex-cornered card: its name (the Cell's name when the fleet
 * knows it, else its id), then one row per dimension (cores, memory, GPUs, model seats, sub-bee
 * slots) with a thin stacked bar of what is in use, granted, held in reserve and free, and the
 * figures in words at the right. The bars are tiny SVGs placed by `layout.ts`, so the widths are
 * the same pure numbers the tests check. Colour never works alone: every bar has its figures, a
 * legend names the four kinds (the reserve is hatched as well as grey), and a hidden table with
 * the same numbers follows for screen readers. An over-committed bar shows a mark where its total
 * ends and the words "over by".
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/capacity/`. Drawn by `forage_screen.tsx` for the
 *   shared pool and again for the hosts; makes no request and holds no state.
 *
 * Key invariants:
 *   - A pure function of its props: no timers, no random numbers; two renders are identical.
 *   - Every bar has a `<title>` with its full text; fills and strokes are `var(--...)` tokens only.
 *   - Text is real text in the page's font, never cut: a card wraps its figures instead.
 *
 * See Also:
 *   - docs/observation/design.md sections 3.4, 4.7 and 4.8.
 */
import { useId } from 'react';
import type { ReactElement } from 'react';
import { IdToken } from '../../../components';
import { BAR_HEIGHT, BAR_WIDTH, BAR_X, KIND_WORD, layoutCapacity } from './layout';
import type { LaidBlock, LaidRow } from './layout';
import type { HostBlock, SegmentKind } from './model';

// Fill per segment kind; the reserve's hatch is drawn per card so each card's pattern id is its own.
const FILL: Readonly<Record<Exclude<SegmentKind, 'reserve'>, string>> = {
  in_use: 'var(--accent)',
  granted: 'var(--ok)',
  free: 'var(--border-soft)',
};

function Legend(props: { hatch: string }): ReactElement {
  return (
    <ul className="forage-legend" aria-label="Capacity legend">
      {(['in_use', 'granted', 'reserve', 'free'] as const).map((kind) => (
        <li key={kind}>
          <svg width="14" height="14" aria-hidden="true">
            <rect
              width="14"
              height="14"
              fill={kind === 'reserve' ? `url(#${props.hatch})` : FILL[kind]}
              stroke="var(--border)"
            />
          </svg>
          {KIND_WORD[kind]}
        </li>
      ))}
    </ul>
  );
}

// One dimension: its label, the bar, its figures.
function Row(props: { laid: LaidRow; hatch: string }): ReactElement {
  const { laid, hatch } = props;
  const text = `${laid.row.label}: ${laid.figures}`;
  return (
    <li className="forage-host-row">
      <span className="forage-host-label">{laid.row.label}</span>
      <svg
        className="forage-bar"
        viewBox={`0 0 ${BAR_X * 2 + BAR_WIDTH} ${BAR_HEIGHT}`}
        role="img"
        aria-label={text}
      >
        <title>{text}</title>
        <rect
          x={BAR_X}
          y={0}
          width={BAR_WIDTH}
          height={BAR_HEIGHT}
          fill="var(--surface-2)"
          stroke="var(--border-soft)"
        />
        {laid.segments.map((s) => (
          <rect
            key={s.kind}
            x={s.x}
            y={0}
            width={s.width}
            height={BAR_HEIGHT}
            fill={s.kind === 'reserve' ? `url(#${hatch})` : FILL[s.kind]}
          />
        ))}
        {laid.limitX !== null ? (
          <line
            x1={laid.limitX}
            x2={laid.limitX}
            y1={0}
            y2={BAR_HEIGHT}
            stroke="var(--alarm)"
            strokeWidth="2"
          />
        ) : null}
      </svg>
      <span className="forage-host-figures">{laid.figures}</span>
    </li>
  );
}

// A host card's title: the fleet's name for the Cell when it has one, else the word "Cell"; the
// Cell id follows as a token either way, so an unnamed host reads as a card and never as a 30
// character line of id (design.md 4.3). The pool is named by its block.
function Title(props: { block: LaidBlock; name: string | null }): ReactElement {
  const { block, name } = props;
  if (block.kind === 'pool') return <>{block.title}</>;
  return (
    <>
      {name ?? <span className="forage-host-kind">Cell</span>}
      <IdToken id={block.id} />
    </>
  );
}

function HostCard(props: { block: LaidBlock; name: string | null; hatch: string }): ReactElement {
  const { block, name, hatch } = props;
  return (
    <li className="forage-host comb-cell">
      <h3 className="forage-host-title">
        <Title block={block} name={name} />
      </h3>
      <ul className="forage-host-rows">
        {block.rows.map((laid) => (
          <Row key={laid.row.key} laid={laid} hatch={hatch} />
        ))}
      </ul>
    </li>
  );
}

export function CapacityDiagram(props: {
  blocks: readonly HostBlock[];
  // What the diagram is, for its name and the hidden table's caption.
  caption?: string;
  // Cell names from the fleet, by Cell id; a host the fleet has not named shows its id.
  names?: ReadonlyMap<string, string>;
}): ReactElement {
  const hatch = useId();
  const layout = layoutCapacity(props.blocks);
  const caption = props.caption ?? 'Capacity per host';
  const label = `${caption}: in use, granted, reserve and free, for ${layout.blocks.length} hosts and pools`;
  return (
    <figure className="forage-figure forage-capacity" role="group" aria-label={label}>
      <svg width="0" height="0" aria-hidden="true" focusable="false">
        <defs>
          <pattern
            id={hatch}
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="6" height="6" fill="var(--surface-2)" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--idle)" strokeWidth="3" />
          </pattern>
        </defs>
      </svg>
      <ul className="forage-hosts">
        {layout.blocks.map((block) => (
          <HostCard
            key={block.id}
            block={block}
            name={props.names?.get(block.id) ?? null}
            hatch={hatch}
          />
        ))}
      </ul>
      <Legend hatch={hatch} />
      <div className="sr-only">
        <table>
          <caption>{`${caption}, in numbers`}</caption>
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
      </div>
    </figure>
  );
}
