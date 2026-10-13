/**
 * The task graph's geometry: which layer each task is in, and where each node and line goes.
 *
 * `layoutGraph` takes the task ids, the dependency edges and one of two modes and returns plain
 * numbers in viewBox units. Wide runs the layers left to right (dependencies on the left), narrow
 * runs them top to bottom with two nodes to a row so the 360 unit viewBox is 360 pixels on a
 * phone and its 12 unit text stays 12 pixels. A task's layer is the length of the longest chain of
 * tasks it waits on; inside a layer the nodes go by task id.
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
 *   - Lines are drawn first, so a line that runs behind a node is hidden by it.
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
  // SVG path data: horizontal and vertical segments only.
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
const GAP = 8; // between nodes of a layer
const LAYER_GAP = 40; // between layers, where the lines run
const NARROW_W = 360;
const NARROW_COLS = 2;

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

function placeNarrow(groups: string[][]): PlacedNode[] {
  const out: PlacedNode[] = [];
  let top = MARGIN;
  groups.forEach((ids, layer) => {
    ids.forEach((id, i) => {
      const row = Math.floor(i / NARROW_COLS);
      const x = MARGIN + (i % NARROW_COLS) * (NODE_W + GAP);
      out.push({ id, layer, x, y: top + row * (NODE_H + GAP), w: NODE_W, h: NODE_H });
    });
    const rows = Math.ceil(ids.length / NARROW_COLS);
    top += rows * NODE_H + (rows - 1) * GAP + LAYER_GAP;
  });
  return out;
}

// An elbow from the end of `from` to the matching end of `to`, its bus just before the target.
function lineBetween(from: PlacedNode, to: PlacedNode, mode: GraphMode): string {
  const forward = to.layer > from.layer;
  if (mode === 'wide') {
    const fy = from.y + from.h / 2;
    const ty = to.y + to.h / 2;
    if (forward) return `M${from.x + from.w} ${fy} H${to.x - LAYER_GAP / 2} V${ty} H${to.x}`;
    const bus = to.x + to.w + LAYER_GAP / 2;
    return `M${from.x} ${fy} H${bus} V${ty} H${to.x + to.w}`;
  }
  const fx = from.x + from.w / 2;
  const tx = to.x + to.w / 2;
  if (forward) return `M${fx} ${from.y + from.h} V${to.y - LAYER_GAP / 2} H${tx} V${to.y}`;
  const bus = to.y + to.h + LAYER_GAP / 2;
  return `M${fx} ${from.y} V${bus} H${tx} V${to.y + to.h}`;
}

export function layoutGraph(
  ids: readonly string[],
  edges: readonly GraphEdge[],
  mode: GraphMode,
): GraphLayout {
  const layers = layersOf(ids, edges);
  const groups = grouped(layers);
  const nodes = mode === 'wide' ? placeWide(groups) : placeNarrow(groups);
  const at = new Map(nodes.map((node) => [node.id, node]));
  const lines: GraphLine[] = [];
  for (const { before, after } of drawable(new Set(layers.keys()), edges)) {
    const from = at.get(before);
    const to = at.get(after);
    if (from && to) lines.push({ key: `${before}>${after}`, d: lineBetween(from, to, mode) });
  }
  const right = nodes.reduce((far, n) => Math.max(far, n.x + n.w), 0);
  const low = nodes.reduce((far, n) => Math.max(far, n.y + n.h), 0);
  return {
    mode,
    width: mode === 'narrow' ? NARROW_W : right + MARGIN,
    height: low + MARGIN,
    layers: groups.length,
    nodes,
    lines,
  };
}
