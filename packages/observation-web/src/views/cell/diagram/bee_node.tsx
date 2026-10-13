/**
 * `BeeNodeView`: one Warden or sub-bee drawn as an SVG node.
 *
 * A node is a link to the bee's page (a sub-bee's thoughts, the Warden's Attendant view). It
 * carries the role glyph and word, the id, what the bee is doing (or where the Warden runs), a
 * status dot with its word, and a context gauge along the bottom edge. A bee in an awake episode
 * has a 2 unit outline in the accent colour, one with an open Alarm on its task has it in the
 * alarm colour and says `Alarm`; the rest have the plain 1 unit border. The Warden's outline is
 * dashed while where it runs is not tracked, so a guess never looks like a fact.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/diagram/`. Drawn by `cell_diagram.tsx` at the
 *   rectangle `layout.ts` chose.
 *
 * Key invariants:
 *   - Every colour is a token (`var(--...)`), so both themes work with no literal.
 *   - The gauge is a `role="meter"` only when both the tokens used and the window are known; an
 *     unreported context draws an empty track and no meter, never a made-up value.
 *   - The visible text is cut to fit; the whole text is in the `<title>` and the link's label.
 *
 * See Also:
 *   - docs/observation/design.md section 4.5.
 */
import type { ReactElement } from 'react';
import { GLYPH_PATHS } from './glyphs';
import type { Placed } from './layout';
import { cut, ID_CUT, LINE_CUT } from './model';
import type { BeeNode, Tone } from './model';

export const TONE_VAR: Readonly<Record<Tone, string>> = {
  ok: 'var(--ok)',
  warn: 'var(--warn)',
  alarm: 'var(--alarm)',
  idle: 'var(--idle)',
};

// The Gauge component's thresholds (design.md 4.2): ok to 70 percent, warn to 90, alarm above.
export function gaugeTone(fraction: number): Tone {
  const percent = Math.round(fraction * 100);
  if (percent > 90) return 'alarm';
  return percent >= 70 ? 'warn' : 'ok';
}

function outlineOf(node: BeeNode): { stroke: string; width: number } {
  if (node.alarmed) return { stroke: 'var(--alarm)', width: 2 };
  return node.awake ? { stroke: 'var(--accent)', width: 2 } : { stroke: 'var(--border)', width: 1 };
}

function ContextGauge(props: { placed: Placed<BeeNode> }): ReactElement {
  const { x, y, w, h, item } = props.placed;
  const trackW = w - 16;
  const clamped = item.fraction === null ? 0 : Math.min(1, Math.max(0, item.fraction));
  const track = (
    <>
      <rect x={x + 8} y={y + h - 12} width={trackW} height={6} rx={3} fill="var(--border-soft)" />
      <rect
        x={x + 8}
        y={y + h - 12}
        width={Math.round(trackW * clamped)}
        height={6}
        rx={3}
        fill={TONE_VAR[gaugeTone(clamped)]}
      />
    </>
  );
  // An unreported context is not a meter: no value to announce.
  if (item.used === null || item.window === null) return <g>{track}</g>;
  return (
    <g
      role="meter"
      aria-label={`Context of ${item.id}`}
      aria-valuenow={item.used}
      aria-valuemin={0}
      aria-valuemax={item.window}
    >
      {track}
    </g>
  );
}

export function BeeNodeView(props: {
  placed: Placed<BeeNode>;
  href: string;
  dashed: boolean;
}): ReactElement {
  const { placed, href, dashed } = props;
  const { x, y, w, h, item } = placed;
  const outline = outlineOf(item);
  return (
    <a href={href} aria-label={item.title} data-bee={item.id} className="dg-node">
      <title>{item.title}</title>
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        rx={6}
        fill="var(--surface)"
        stroke={outline.stroke}
        strokeWidth={outline.width}
        strokeDasharray={dashed ? '4 3' : undefined}
      />
      <path
        d={GLYPH_PATHS[item.glyph]}
        transform={`translate(${x + 8} ${y + 6})`}
        fill="none"
        stroke="var(--text)"
        strokeWidth={1.5}
      />
      <text className="dg-role" x={x + 30} y={y + 18}>
        {item.roleLabel}
      </text>
      <text className="dg-id" x={x + 8} y={y + 34}>
        {cut(item.id, ID_CUT)}
      </text>
      <text className="dg-line" x={x + 8} y={y + 50}>
        {cut(item.line, LINE_CUT)}
      </text>
      <circle cx={x + 14} cy={y + 64} r={5} fill={TONE_VAR[item.tone]} />
      <text className="dg-word" x={x + 24} y={y + 68}>
        {item.word}
      </text>
      {item.alarmed ? (
        <text className="dg-alarm" x={x + w - 8} y={y + 68} textAnchor="end">
          Alarm
        </text>
      ) : null}
      <ContextGauge placed={placed} />
    </a>
  );
}
