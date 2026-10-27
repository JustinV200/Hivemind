/**
 * The task graph's geometry: which layer each task is in, and where each node and line goes.
 *
 * `layoutGraph` takes the task ids, the dependency edges, one of two modes and the pane's measured
 * width, and returns plain numbers in viewBox units. Wide runs the layers left to right
 * (dependencies on the left) at the drawing's natural size; narrow runs them top to bottom on a
 * canvas exactly as wide as the pane, with as many nodes to a row as fit it (one on a phone), so
 * one unit is one pixel and the 12 unit text stays 12 px (design.md 4.8). A task's layer is the
 * length of the longest chain of tasks it waits on; inside a layer the nodes go by task id. A line
 * is the plain elbow (out of its task's far edge, along to the bus in the gap before the task
 * that waits, across, in) where that runs through no node; otherwise it leaves its task by the
 * lane beside it (the gap between rows when wide, between columns when narrow), runs along that
 * lane to the bus, across the bus to the lane beside the waiting task, and in through its side:
 * every node of every layer sits on the same row and column grid, so a lane and a bus never
 * cross a node, whatever layers the line spans or how a layer wraps.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/graph/`. Called by `task_graph.tsx`; the
 *   result is drawn by `task_node.tsx` and `task_graph.tsx`.
 *
 * Key invariants:
 *   - Pure and deterministic: the same ids, edges and mode give the same numbers, all whole, no
 *     matter the order the ids or edges arrive in.
 *   - It always terminates: each pass of the layering removes at least one task, so a cycle in
 *     the edges (which a healthy graph never has) breaks at its smallest id and cannot loop.
 *   - An edge naming a task that is not in the graph, a task waiting on itself, and a repeated
 *     edge are dropped: they have nothing to draw between.
 *   - A line never crosses a node: it travels only in the gaps of the grid the nodes are on.
 *
 * See Also:
 *   - docs/observation/design.md section 4.8, for the rules every diagram follows.
 */
export type GraphMode = 'wide' | 'narrow';

// `after` starts only once `before` has succeeded, so the line runs from `before` to `after`.
export interface GraphEdge {
  before: string;
  after: string;
}
export interface PlacedNode {
  id: string;
  layer: number;
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface GraphLine {
  key: string;
  // SVG path data: axis-aligned segments only, ending at the task that waits.
  d: string;
}
export interface GraphLayout {
  mode: GraphMode;
  width: number;
  height: number;
  layers: number;
  nodes: PlacedNode[];
  lines: GraphLine[];
}

export const NODE_W = 164;
export const NODE_H = 60; // three 16 unit text lines
const MARGIN = 12;
const GAP = 8; // between nodes of a layer, where a line's lane runs
const LAYER_GAP = 40; // between layers, where a line's bus runs
export const WIDE_FROM = 720; // The narrowest pane that gets the left to right layout.
const NARROW_W = 360; // The narrow canvas before the pane is measured: the design's first phone.
const NARROW_MIN = NODE_W + 2 * MARGIN; // One node always fits; below this the pane scrolls.

const byCodePoint = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

// The edges that can be drawn, each once, in a fixed order.
function drawable(ids: ReadonlySet<string>, edges: readonly GraphEdge[]): GraphEdge[] {
  const seen = new Set<string>();
  const kept: GraphEdge[] = [];
  for (const edge of edges) {
    const key = `${edge.before}\u0000${edge.after}`;
    const usable = edge.before !== edge.after && ids.has(edge.before) && ids.has(edge.after);
    if (usable && !seen.has(key)) {
      seen.add(key);
      kept.push(edge);
    }
  }
  return kept.sort((a, b) => byCodePoint(a.before, b.before) || byCodePoint(a.after, b.after));
}

// Layer by peeling: every task nothing is left waiting on goes in the next layer, which is
// exactly the longest chain of waits before it. With only a cycle left, its smallest id goes.
export function layersOf(ids: readonly string[], edges: readonly GraphEdge[]): Map<string, number> {
  const sorted = [...new Set(ids)].sort(byCodePoint);
  const kept = drawable(new Set(sorted), edges);
  const waiting = new Map<string, number>(sorted.map((id) => [id, 0]));
  const followers = new Map<string, string[]>();
  for (const { before, after } of kept) {
    waiting.set(after, (waiting.get(after) ?? 0) + 1);
    followers.set(before, [...(followers.get(before) ?? []), after]);
  }
  const layers = new Map<string, number>();
  let remaining = sorted;
  for (let layer = 0; remaining.length > 0; layer++) {
    const free = remaining.filter((id) => waiting.get(id) === 0);
    // Nothing is free only when what is left is a cycle (or hangs off one): break it at one id.
    const peeled = free.length > 0 ? free : [remaining[0] as string];
    for (const id of peeled) layers.set(id, layer);
    remaining = remaining.filter((id) => !layers.has(id));
    for (const id of peeled) {
      for (const next of followers.get(id) ?? []) waiting.set(next, (waiting.get(next) ?? 1) - 1);
    }
  }
  return layers;
}

// Each layer's task ids, in layer order and then id order.
function grouped(layers: Map<string, number>): string[][] {
  const out: string[][] = [];
  for (const id of [...layers.keys()].sort(byCodePoint)) {
    const layer = layers.get(id) as number;
    (out[layer] ??= []).push(id);
  }
  return out;
}

function placeWide(groups: string[][]): PlacedNode[] {
  return groups.flatMap((ids, layer) =>
    ids.map((id, i) => ({
      id,
      layer,
      x: MARGIN + layer * (NODE_W + LAYER_GAP),
      y: MARGIN + i * (NODE_H + GAP),
      w: NODE_W,
      h: NODE_H,
    })),
  );
}

// Layers top to bottom, each wrapped to as many nodes across as the canvas holds (at least one).
function placeNarrow(groups: string[][], width: number): PlacedNode[] {
  const cols = Math.max(1, Math.floor((width - 2 * MARGIN + GAP) / (NODE_W + GAP)));
  const out: PlacedNode[] = [];
  let top = MARGIN;
  groups.forEach((ids, layer) => {
    ids.forEach((id, i) => {
      const row = Math.floor(i / cols);
      const x = MARGIN + (i % cols) * (NODE_W + GAP);
      out.push({ id, layer, x, y: top + row * (NODE_H + GAP), w: NODE_W, h: NODE_H });
    });
    const rows = Math.ceil(ids.length / cols);
    top += rows * NODE_H + (rows - 1) * GAP + LAYER_GAP;
  });
  return out;
}

// A node's extent along the flow (the layer axis: x when wide, y when narrow) and across it.
interface Span {
  along: [number, number];
  across: [number, number];
}

function spanOf(node: PlacedNode, mode: GraphMode): Span {
  const xs: [number, number] = [node.x, node.x + node.w];
  const ys: [number, number] = [node.y, node.y + node.h];
  return mode === 'wide' ? { along: xs, across: ys } : { along: ys, across: xs };
}

const mid = ([low, high]: [number, number]): number => (low + high) / 2;

// What the router knows besides the two nodes: every node's span (to keep a line out of them)
// and where each layer begins and ends along the flow, so a bus runs in the gap before or after
// a whole layer, never between two wrapped rows of one.
interface Grid {
  mode: GraphMode;
  spans: Span[];
  layers: Map<number, [number, number]>;
}

function gridOf(nodes: readonly PlacedNode[], mode: GraphMode): Grid {
  const layers = new Map<number, [number, number]>();
  for (const node of nodes) {
    const { along } = spanOf(node, mode);
    const held = layers.get(node.layer) ?? [Infinity, -Infinity];
    layers.set(node.layer, [Math.min(held[0], along[0]), Math.max(held[1], along[1])]);
  }
  return { mode, spans: nodes.map((node) => spanOf(node, mode)), layers };
}

// Whether a segment along the flow at `across`, between the two along values, runs through the
// inside of any node (touching an edge is not crossing, which is how it leaves its own node).
function blocked(grid: Grid, across: number, from: number, to: number): boolean {
  const [low, high] = from < to ? [from, to] : [to, from];
  return grid.spans.some(
    (s) => s.across[0] < across && across < s.across[1] && s.along[0] < high && low < s.along[1],
  );
}

// The line from `from` to `to`. The plain elbow leaves the source's far edge, runs along to the
// bus in the gap before the target's layer (after it, for a line back up the layers), across the
// bus and along into the target. Where that would run through another node (a wrapped row of the
// source's layer, a layer in between), the line takes the lanes instead: out of the source's
// side into the gap beside it, along that gap to the bus, across to the gap beside the target
// and in through its side; every node of every layer sits on one row and column grid, so those
// gaps are empty. Each point is (across, along), mapped to x and y by the mode.
function lineBetween(from: PlacedNode, to: PlacedNode, grid: Grid): string {
  const a = spanOf(from, grid.mode);
  const b = spanOf(to, grid.mode);
  const forward = to.layer > from.layer;
  const layer = grid.layers.get(to.layer) ?? b.along;
  const bus = forward ? layer[0] - LAYER_GAP / 2 : layer[1] + LAYER_GAP / 2;
  const exit = forward ? a.along[1] : a.along[0];
  const entry = forward ? b.along[0] : b.along[1];
  const clear =
    !blocked(grid, mid(a.across), exit, bus) && !blocked(grid, mid(b.across), bus, entry);
  const points: [number, number][] = clear
    ? [
        [mid(a.across), exit],
        [mid(a.across), bus],
        [mid(b.across), bus],
        [mid(b.across), entry],
      ]
    : [
        [a.across[1], mid(a.along)],
        [a.across[1] + GAP / 2, mid(a.along)],
        [a.across[1] + GAP / 2, bus],
        [b.across[1] + GAP / 2, bus],
        [b.across[1] + GAP / 2, mid(b.along)],
        [b.across[1], mid(b.along)],
      ];
  const xy = ([across, along]: [number, number]): string =>
    grid.mode === 'wide' ? `${along} ${across}` : `${across} ${along}`;
  return `M${points.map(xy).join(' L')}`;
}

export function layoutGraph(
  ids: readonly string[],
  edges: readonly GraphEdge[],
  mode: GraphMode,
  // The pane's measured width in px; null before it is measured (the 360 unit canvas then).
  paneWidth: number | null = null,
): GraphLayout {
  const width = Math.max(NARROW_MIN, paneWidth ?? NARROW_W);
  const layers = layersOf(ids, edges);
  const groups = grouped(layers);
  const nodes = mode === 'wide' ? placeWide(groups) : placeNarrow(groups, width);
  const at = new Map(nodes.map((node) => [node.id, node]));
  const grid = gridOf(nodes, mode);
  const lines: GraphLine[] = [];
  for (const { before, after } of drawable(new Set(layers.keys()), edges)) {
    const from = at.get(before);
    const to = at.get(after);
    if (from && to) lines.push({ key: `${before}>${after}`, d: lineBetween(from, to, grid) });
  }
  const right = nodes.reduce((far, n) => Math.max(far, n.x + n.w), 0);
  const low = nodes.reduce((far, n) => Math.max(far, n.y + n.h), 0);
  return {
    mode,
    width: mode === 'narrow' ? width : right + MARGIN,
    height: low + MARGIN,
    layers: groups.length,
    nodes,
    lines,
  };
}
