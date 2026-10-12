/**
 * The diagram's geometry: where each node, box, frame and line goes, as plain numbers.
 *
 * `layoutDiagram` takes the model and one of two modes and returns rectangles and line paths in
 * viewBox units. Wide puts the fact boxes in a column to the right of the Cell frame; narrow
 * stacks them below it in two columns, so the viewBox is 360 units across and its 12 unit text is
 * 12 pixels on a phone. The Warden sits inside the frame when it runs on the Cell and above it
 * (outside, on the Hive Stand) otherwise, which is how the frame says where the Warden runs.
 * Sub-bees fill rows of as many nodes as the frame is wide enough for (1 to 6), centred per row.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/diagram/`. Called by `cell_diagram.tsx`; the
 *   result is drawn by `bee_node.tsx` and `cell_diagram.tsx`.
 *
 * Key invariants:
 *   - Pure and deterministic: the same model and mode give the same numbers, all whole.
 *   - Nothing leaves the viewBox: every rectangle is inside `width` by `height`.
 *   - Nodes are drawn after lines, so a line that runs behind a node is hidden by it.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.5, 4.6 and 4.8.
 */
import type { BeeNode, DiagramModel, FactBox } from './model';

export type LayoutMode = 'wide' | 'narrow';

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface Placed<T> extends Rect {
  item: T;
}
export interface Link {
  key: string;
  // SVG path data: vertical-then-horizontal segments only.
  d: string;
}
export interface Layout {
  mode: LayoutMode;
  width: number;
  height: number;
  frame: Rect;
  wardenOutside: boolean;
  warden: Placed<BeeNode> | null;
  bees: Placed<BeeNode>[];
  boxes: Placed<FactBox>[];
  links: Link[];
}

export const NODE_W = 160;
export const NODE_H = 88; // four 16 unit text lines and the gauge along the bottom edge
export const WARDEN_W = 200;
const GAP = 8; // between nodes in a row
const ROW_GAP = 24; // between rows, where the link bus runs
const PAD = 10; // inside the frame
const MARGIN = 4; // around the drawing
const LABEL_H = 24; // the frame's label row
const LINK_GAP = 28; // between the Warden and the first row
const BOX_H = 60;
const SIDE_GAP = 20; // between the frame and the box column
const WIDE_W = 744;
const NARROW_W = 360;
const FRAME_W: Readonly<Record<LayoutMode, number>> = { wide: 520, narrow: 352 };
const EMPTY_H = 32; // the frame's body when the Cell has no sub-bees to show

const clamp = (n: number, low: number, high: number): number => Math.min(high, Math.max(low, n));

// How many nodes fit across the frame's inside, from 1 to 6.
export function columnsFor(frameWidth: number): number {
  return clamp(Math.floor((frameWidth - 2 * PAD + GAP) / (NODE_W + GAP)), 1, 6);
}

// Each sub-bee's corner: row by row, every row centred in the frame's inside.
function bePositions(count: number, cols: number, frame: Rect, top: number): Placed<null>[] {
  const out: Placed<null>[] = [];
  for (let i = 0; i < count; i++) {
    const row = Math.floor(i / cols);
    const inRow = Math.min(cols, count - row * cols);
    const rowW = inRow * NODE_W + (inRow - 1) * GAP;
    const x = frame.x + Math.floor((frame.w - rowW) / 2) + (i % cols) * (NODE_W + GAP);
    out.push({ item: null, x, y: top + row * (NODE_H + ROW_GAP), w: NODE_W, h: NODE_H });
  }
  return out;
}

// A line from the Warden's bottom edge down to a row's bus, across, and down into the node.
function treeLink(warden: Rect, node: Rect, id: string): Link {
  const from = warden.x + warden.w / 2;
  const to = node.x + node.w / 2;
  const bus = node.y - ROW_GAP / 2;
  return { key: `tree:${id}`, d: `M${from} ${warden.y + warden.h} V${bus} H${to} V${node.y}` };
}

interface Body {
  frame: Rect;
  warden: Placed<BeeNode> | null;
  bees: Placed<BeeNode>[];
  wardenOutside: boolean;
}

// The Cell frame with the Warden and the sub-bees placed, top to bottom.
function placeBody(model: DiagramModel, mode: LayoutMode): Body {
  const frameW = FRAME_W[mode];
  const outside = model.warden !== null && model.wardenRuns !== 'on_cell';
  const inside = model.warden !== null && !outside;
  const frame: Rect = { x: MARGIN, y: MARGIN, w: frameW, h: 0 };
  const centred = (w: number): number => frame.x + Math.floor((frameW - w) / 2);
  let warden: Placed<BeeNode> | null = null;
  // Outside the frame the Warden sits above it, and the frame starts below its link.
  if (model.warden && outside) {
    warden = { item: model.warden, x: centred(WARDEN_W), y: MARGIN, w: WARDEN_W, h: NODE_H };
    frame.y = MARGIN + NODE_H + LINK_GAP;
  }
  // What sits above the sub-bees: the frame's label, then (inside) the Warden under it.
  let above = frame.y + LABEL_H;
  if (model.warden && inside) {
    warden = { item: model.warden, x: centred(WARDEN_W), y: above, w: WARDEN_W, h: NODE_H };
    above += NODE_H;
  }
  const top = warden && inside ? above + LINK_GAP : above;
  const spots = bePositions(model.bees.length, columnsFor(frameW), frame, top);
  const bees = spots.map((s, i) => ({ ...s, item: model.bees[i] as BeeNode }));
  // The frame ends under its lowest node, or after a short empty body when there are none.
  const bottom = bees.reduce((low, b) => Math.max(low, b.y + b.h), above + EMPTY_H);
  frame.h = bottom + PAD - frame.y;
  return { frame, warden, bees, wardenOutside: outside };
}

// Where each fact box goes: a column right of the frame (wide) or two columns below it (narrow).
function placeBoxes(boxes: FactBox[], body: Body, mode: LayoutMode): Placed<FactBox>[] {
  const { frame } = body;
  if (mode === 'wide') {
    const x = frame.x + frame.w + SIDE_GAP;
    return boxes.map((item, i) => ({
      item,
      x,
      y: MARGIN + i * (BOX_H + GAP),
      w: WIDE_W - MARGIN - x,
      h: BOX_H,
    }));
  }
  const w = Math.floor((NARROW_W - 2 * MARGIN - GAP) / 2);
  const top = frame.y + frame.h + LINK_GAP;
  return boxes.map((item, i) => ({
    item,
    x: MARGIN + (i % 2) * (w + GAP),
    y: top + Math.floor(i / 2) * (BOX_H + GAP),
    w,
    h: BOX_H,
  }));
}

// Joins each box to what it concerns: the Warden for the session and the Exoskeleton, the frame
// for the lease and the models. Below the frame (narrow) a short stub up to its edge does it.
function boxLinks(boxes: Placed<FactBox>[], body: Body, mode: LayoutMode): Link[] {
  const { frame, warden } = body;
  return boxes.map((box) => {
    const key = `box:${box.item.key}`;
    if (mode === 'narrow') {
      const x = box.x + Math.floor(box.w / 2);
      return { key, d: `M${x} ${box.y} V${frame.y + frame.h}` };
    }
    const y = box.y + box.h / 2;
    const toWarden = warden !== null && ['session', 'exoskeleton'].includes(box.item.key);
    if (!toWarden) return { key, d: `M${box.x} ${y} H${frame.x + frame.w}` };
    const mid = frame.x + frame.w + SIDE_GAP / 2;
    const ty = warden.y + warden.h / 2;
    return { key, d: `M${box.x} ${y} H${mid} V${ty} H${warden.x + warden.w}` };
  });
}

export function layoutDiagram(model: DiagramModel, mode: LayoutMode): Layout {
  const body = placeBody(model, mode);
  const boxes = placeBoxes(model.boxes, body, mode);
  const links: Link[] = [];
  // Tree lines first, so nodes drawn later hide the stretches that pass behind them.
  const { warden } = body;
  if (warden) body.bees.forEach((b) => links.push(treeLink(warden, b, b.item.id)));
  links.push(...boxLinks(boxes, body, mode));
  const lowest = [body.frame, ...boxes].reduce((low, r) => Math.max(low, r.y + r.h), 0);
  return {
    mode,
    width: mode === 'wide' ? WIDE_W : NARROW_W,
    height: lowest + MARGIN,
    frame: body.frame,
    wardenOutside: body.wardenOutside,
    warden: body.warden,
    bees: body.bees,
    boxes,
    links,
  };
}
