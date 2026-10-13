/**
 * The flow layout: pure boxes and ribbons for the pool, Warden and grant columns.
 *
 * Three columns of boxes on a 360 unit canvas, left to right: the Queen's shared pool (the Royal
 * Reserve, what is granted, what is free), one box per Warden, and one box per grant grouped under
 * its Warden. A box's height is proportional to its amount on one scale for the whole diagram
 * (with a floor so a small box keeps room for its label), and a ribbon joins each slice of a box to
 * the next column with the same share of the height. Used amounts fill a box from its bottom. Every
 * position comes from sorted ids and fixed sizes, so equal models give equal layouts.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/flow/`. Takes the model of `model.ts`; its output
 *   is drawn by `diagram.tsx` and checked by tests as plain numbers.
 *
 * Key invariants:
 *   - Pure and deterministic; numbers rounded to two decimals.
 *   - Box heights are `max(MIN_BOX, amount * scale)`; with no amounts at all, every box is `MIN_BOX`.
 *   - A ribbon never leaves its source box: slices of one box tile it without overlap.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.7 and 4.8.
 */
import { cut } from '../format';
import { formatAmount } from './model';
import type { FlowModel } from './model';

export const FLOW_WIDTH = 360;
const NODE_W = 14;
const COLUMN_X: readonly [number, number, number] = [0, 123, 246];
const TARGET_H = 200; // The tallest column's boxes together, before the floor and gaps.
export const MIN_BOX = 14;
const GAP = 6;
const PAD = 6;
const LABEL_CHARS = 14; // What fits between a box and the next column at 12 units of text.

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
  label: string;
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
  label: string;
  title: string;
}

function poolItems(model: FlowModel): Item[] {
  const text = (n: number): string => formatAmount(model.unit, n);
  const item = (kind: NodeKind, word: string, amount: number): Item => ({
    id: `pool-${kind}`,
    kind,
    amount,
    used: null,
    near: false,
    label: `${word} ${text(amount)}`,
    title: `${word} in the shared pool: ${text(amount)}`,
  });
  const items = [
    item('reserve', 'Reserve', model.reserve),
    item('granted', 'Granted', model.granted),
  ];
  if (model.free !== null) items.push(item('free', 'Free', model.free));
  return items.filter((i) => i.amount > 0);
}

function wardenItems(model: FlowModel): Item[] {
  return model.wardens.map((w) => ({
    id: w.id,
    kind: 'warden',
    amount: w.amount,
    used: w.used,
    near: w.grants.some((g) => g.near),
    label: cut(w.id, LABEL_CHARS),
    title: `Warden ${w.id} on ${w.cellId || 'an unknown Cell'}${w.state ? ` (${w.state})` : ''}: ${formatAmount(model.unit, w.amount)} granted`,
  }));
}

function grantItems(model: FlowModel): Item[] {
  return model.wardens.flatMap((w) =>
    w.grants.map((g) => ({
      id: g.id,
      kind: 'grant' as const,
      amount: g.amount,
      used: g.used,
      near: g.near,
      // A grant nearing expiry says so in words, so the warn colour is never the only signal.
      label: g.near ? `${cut(g.label, LABEL_CHARS - 5)} soon` : cut(g.label, LABEL_CHARS),
      title: `Grant ${g.id} for ${g.label}: ${formatAmount(model.unit, g.amount)}${g.used === null ? '' : `, used ${formatAmount(model.unit, g.used)}`}, expires ${g.expiresAt}`,
    })),
  );
}

// Stacks a column's items top to bottom on the shared scale, with a gap between boxes.
function stack(items: readonly Item[], column: 0 | 1 | 2, scale: number): FlowBox[] {
  let y = PAD;
  return items.map((item) => {
    const h = round2(Math.max(MIN_BOX, item.amount * scale));
    const filled = item.used !== null && item.amount > 0 ? Math.min(1, item.used / item.amount) : 0;
    const box: FlowBox = {
      id: item.id,
      kind: item.kind,
      column,
      x: COLUMN_X[column],
      y: round2(y),
      w: NODE_W,
      h,
      amount: item.amount,
      usedH: round2(h * filled),
      near: item.near,
      label: item.label,
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

export function layoutFlow(model: FlowModel): FlowLayout {
  const pool = poolItems(model);
  const wardens = wardenItems(model);
  const grants = grantItems(model);
  const total = (items: readonly Item[]): number => items.reduce((a, i) => a + i.amount, 0);
  const tallest = Math.max(total(pool), total(wardens), total(grants));
  const scale = tallest > 0 ? TARGET_H / tallest : 0;
  const [c0, c1, c2] = [stack(pool, 0, scale), stack(wardens, 1, scale), stack(grants, 2, scale)];
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
    width: FLOW_WIDTH,
    height: round2(Math.max(0, ...bottoms) + PAD),
    scale,
    boxes: [...c0, ...c1, ...c2],
    ribbons,
  };
}
