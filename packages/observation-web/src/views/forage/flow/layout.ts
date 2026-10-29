/**
 * The flow layout: pure boxes and ribbons for the pool, Warden and grant columns.
 *
 * Three labelled columns of boxes, left to right: the Queen's shared pool (the Royal Reserve,
 * what is granted, what is free), one box per Warden, and one box per grant grouped under its
 * Warden. On the wide canvas (480 units, a pane of at least that many px) the pool's labels sit
 * to the left of its boxes and the others to the right, so no label lies on the ribbons that
 * leave the pool. A narrower pane gets a canvas exactly as wide as itself, so its 12 unit text is
 * 12 px (design.md 4.8): every label then sits to the left of its box and the pool's amount goes
 * under its word rather than being cut away, and the ribbons pass under the Warden and grant
 * labels (the drawing gives the words a halo). A grant nearing expiry carries "soon" under its
 * name on either canvas, so the name keeps its tail. A box's height is proportional to its amount
 * on one scale for the whole diagram, with a floor of one line per line of its label so a small
 * box keeps room for its words, and a ribbon joins each slice of a box to the next column with
 * the same share of the height. Used amounts fill a box from its bottom. Every position comes
 * from sorted ids, fixed sizes and the one width, so equal models on equal panes give equal
 * layouts.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/flow/`. Takes the model of `model.ts`; its output
 *   is drawn by `diagram.tsx` and checked by tests as plain numbers.
 *
 * Key invariants:
 *   - Pure and deterministic; numbers rounded to two decimals; the wide canvas keeps the columns
 *     at 90, 232 and 374 units.
 *   - Box heights are `max(LINE_H * lines, amount * scale)`; with no amounts at all, every box is
 *     as tall as its label (`MIN_BOX` for a one-line label).
 *   - A ribbon never leaves its source box: slices of one box tile it without overlap.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.7 and 4.8.
 */
import { idText } from '../../../components/id_token';
import { plainCode } from '../../../components/words';
import { cut } from '../format';
import { formatAmount } from './model';
import type { FlowModel } from './model';

export const FLOW_WIDTH = 480; // The wide canvas; a pane at least this wide draws it.
export const FLOW_MIN = 240; // Below this the drawing scrolls in its pane rather than cut further.
export const NODE_W = 14;
export const COLUMN_HEADINGS: readonly [string, string, string] = [
  'Shared pool',
  'Wardens',
  'Grants',
];
export const HEAD_H = 20; // The column headings' row, above the first box.
const TARGET_H = 200; // The tallest column's boxes together, before the floor and gaps.
export const LINE_H = 14; // One line of 12 unit label text with its leading.
export const MIN_BOX = LINE_H; // The floor of a box with a one-line label.
const GAP = 6;
export const PAD = 6;
const CHAR_W = 6.6; // Units one character of 12 unit sans text takes, on average.
const LABEL_PAD = 4; // Between a label and its box.
const LABEL_CHARS = 13; // The most a label keeps; the wide canvas has room for all of it.
const RIBBON_W = 38; // The clear run of ribbon between a label and the next column's box.
const SOON = 'soon'; // The word a grant nearing expiry carries, so warn is never colour alone.

// The columns' geometry for a canvas width: where each column's boxes stand, how many characters
// a label keeps, whether a column's labels sit left of its boxes, and whether a label stacks its
// parts. Wide puts the Warden and grant labels to the right, in the gaps, which costs a fourth
// label width, and keeps the pool's word and amount on one line; narrow puts every label to the
// left, gives the characters whatever three label widths leave, and stacks the amount under the
// word, since eight characters hold one of them. "soon" is under a grant's name on both.
export interface Geometry {
  width: number;
  columns: readonly [number, number, number];
  chars: number;
  labelLeft: readonly [boolean, boolean, boolean];
  stacked: boolean;
}

export function geometryFor(width: number): Geometry {
  const wide = width >= FLOW_WIDTH;
  const labels = wide ? 4 : 3;
  const room = width - 3 * NODE_W - 2 * RIBBON_W - labels * LABEL_PAD;
  const chars = Math.min(LABEL_CHARS, Math.floor(room / (labels * CHAR_W)));
  const labelW = Math.round(chars * CHAR_W + LABEL_PAD);
  const pitch = NODE_W + RIBBON_W + labelW;
  return {
    width: wide ? FLOW_WIDTH : width,
    columns: [labelW, labelW + pitch, labelW + 2 * pitch],
    chars,
    labelLeft: [true, !wide, !wide],
    stacked: !wide,
  };
}

export type NodeKind = 'reserve' | 'granted' | 'free' | 'warden' | 'grant';

export interface FlowBox {
  id: string;
  kind: NodeKind;
  column: 0 | 1 | 2;
  x: number;
  y: number;
  w: number;
  h: number;
  amount: number;
  // Height of the used part, filled from the bottom; 0 when no use figure exists.
  usedH: number;
  near: boolean;
  // The label's lines, top to bottom, centred on the box; one on the wide canvas.
  lines: readonly string[];
  // Whether the label stands to the left of the box (right-aligned to it) or to its right.
  labelLeft: boolean;
  title: string;
}

export interface FlowRibbon {
  id: string;
  path: string;
  near: boolean;
}

export interface FlowLayout {
  width: number;
  height: number;
  scale: number;
  // Where each column's boxes stand and which side its labels take, for the headings.
  columns: readonly [number, number, number];
  labelLeft: readonly [boolean, boolean, boolean];
  boxes: FlowBox[];
  ribbons: FlowRibbon[];
}

const round2 = (n: number): number => Math.round(n * 100) / 100;

interface Item {
  id: string;
  kind: NodeKind;
  amount: number;
  used: number | null;
  near: boolean;
  lines: readonly string[];
  title: string;
}

// An id as the tables show it, its kind and ULID tail (design.md 4.3); where even that is too
// long for the canvas, the tail alone, which is the part that tells ids apart.
function idLabel(id: string, chars: number): string {
  const text = idText(id);
  if (text.length <= chars) return text;
  const tail = text.indexOf('…');
  return cut(tail > 0 ? text.slice(tail) : text, chars);
}

// A label's parts as its lines: one line of them all on the wide canvas, one line each when stacked.
function linesOf(geo: Geometry, ...parts: string[]): string[] {
  const kept = geo.stacked ? parts : [parts.join(' ')];
  return kept.map((part) => cut(part, geo.chars));
}

function poolItems(model: FlowModel, geo: Geometry): Item[] {
  const text = (n: number): string => formatAmount(model.unit, n);
  const item = (kind: NodeKind, word: string, amount: number): Item => ({
    id: `pool-${kind}`,
    kind,
    amount,
    used: null,
    near: false,
    lines: linesOf(geo, word, text(amount)),
    title: `${word} in the shared pool: ${text(amount)}`,
  });
  const items = [
    item('reserve', 'Reserve', model.reserve),
    item('granted', 'Granted', model.granted),
  ];
  if (model.free !== null) items.push(item('free', 'Free', model.free));
  return items.filter((i) => i.amount > 0);
}

function wardenItems(model: FlowModel, geo: Geometry): Item[] {
  return model.wardens.map((w) => ({
    id: w.id,
    kind: 'warden',
    amount: w.amount,
    used: w.used,
    near: w.grants.some((g) => g.near),
    lines: [idLabel(w.id, geo.chars)],
    // The state is read aloud and listed in the hidden table, so it is a word, not its code.
    title: `Warden ${w.id} on ${w.cellId || 'an unknown Cell'}${w.state ? ` (${plainCode(w.state)})` : ''}: ${formatAmount(model.unit, w.amount)} granted`,
  }));
}

// A grant's label is its task; one nearing expiry says so in words, so the warn colour is never
// the only signal. The word goes on a line of its own on either canvas, since beside the name it
// would cost the name its tail even on the wide one.
function grantLines(label: string, near: boolean, geo: Geometry): string[] {
  const name = idLabel(label, geo.chars);
  return near ? [name, SOON] : [name];
}

function grantItems(model: FlowModel, geo: Geometry): Item[] {
  return model.wardens.flatMap((w) =>
    w.grants.map((g) => ({
      id: g.id,
      kind: 'grant' as const,
      amount: g.amount,
      used: g.used,
      near: g.near,
      lines: grantLines(g.label, g.near, geo),
      title: `Grant ${g.id} for ${g.label}: ${formatAmount(model.unit, g.amount)}${g.used === null ? '' : `, used ${formatAmount(model.unit, g.used)}`}, expires ${g.expiresAt}`,
    })),
  );
}

// Stacks a column's items top to bottom under the headings on the shared scale, with a gap between.
function stack(items: readonly Item[], column: 0 | 1 | 2, scale: number, geo: Geometry): FlowBox[] {
  let y = PAD + HEAD_H;
  return items.map((item) => {
    // The floor is the label's height, so two stacked lines never spill onto the next box's label.
    const h = round2(Math.max(LINE_H * item.lines.length, item.amount * scale));
    const filled = item.used !== null && item.amount > 0 ? Math.min(1, item.used / item.amount) : 0;
    const box: FlowBox = {
      id: item.id,
      kind: item.kind,
      column,
      x: geo.columns[column],
      y: round2(y),
      w: NODE_W,
      h,
      amount: item.amount,
      usedH: round2(h * filled),
      near: item.near,
      lines: item.lines,
      labelLeft: geo.labelLeft[column],
      title: item.title,
    };
    y += h + GAP;
    return box;
  });
}

// A band from the right edge of one slice of a box to the left edge of another.
export function ribbonPath(
  from: FlowBox,
  a: [number, number],
  to: FlowBox,
  b: [number, number],
): string {
  const x0 = from.x + from.w;
  const x1 = to.x;
  const mid = round2((x0 + x1) / 2);
  return (
    `M${x0} ${a[0]} C${mid} ${a[0]} ${mid} ${b[0]} ${x1} ${b[0]} ` +
    `L${x1} ${b[1]} C${mid} ${b[1]} ${mid} ${a[1]} ${x0} ${a[1]} Z`
  );
}

// Splits a box's height among parts by share; with nothing to share, each part is a hairline.
function slices(box: FlowBox, parts: readonly number[]): [number, number][] {
  const total = parts.reduce((a, p) => a + p, 0);
  let at = box.y;
  return parts.map((part) => {
    const span = total > 0 ? (part / total) * box.h : 0;
    const slice: [number, number] = [round2(at), round2(at + span)];
    at += span;
    return slice;
  });
}

function join(from: FlowBox | undefined, targets: readonly FlowBox[]): FlowRibbon[] {
  if (!from) return [];
  const cuts = slices(
    from,
    targets.map((t) => t.amount),
  );
  return targets.map((to, i) => ({
    id: `${from.id}>${to.id}`,
    path: ribbonPath(from, cuts[i] ?? [from.y, from.y], to, [to.y, round2(to.y + to.h)]),
    near: to.near,
  }));
}

export function layoutFlow(
  model: FlowModel,
  // The pane's measured width in px; the wide canvas until it is measured or when it fits.
  paneWidth: number | null = null,
): FlowLayout {
  const geo = geometryFor(Math.max(FLOW_MIN, paneWidth ?? FLOW_WIDTH));
  const pool = poolItems(model, geo);
  const wardens = wardenItems(model, geo);
  const grants = grantItems(model, geo);
  const total = (items: readonly Item[]): number => items.reduce((a, i) => a + i.amount, 0);
  const tallest = Math.max(total(pool), total(wardens), total(grants));
  const scale = tallest > 0 ? TARGET_H / tallest : 0;
  const [c0, c1, c2] = [
    stack(pool, 0, scale, geo),
    stack(wardens, 1, scale, geo),
    stack(grants, 2, scale, geo),
  ];
  // Each Warden's box feeds only the grant boxes it holds.
  const held = new Map(model.wardens.map((w) => [w.id, new Set(w.grants.map((g) => g.id))]));
  const ribbons = [
    ...join(
      c0.find((b) => b.kind === 'granted'),
      c1,
    ),
    ...c1.flatMap((w) =>
      join(
        w,
        c2.filter((g) => held.get(w.id)?.has(g.id)),
      ),
    ),
  ];
  const bottoms = [...c0, ...c1, ...c2].map((b) => b.y + b.h);
  return {
    width: geo.width,
    height: round2(Math.max(PAD + HEAD_H, ...bottoms) + PAD),
    scale,
    columns: geo.columns,
    labelLeft: geo.labelLeft,
    boxes: [...c0, ...c1, ...c2],
    ribbons,
  };
}
