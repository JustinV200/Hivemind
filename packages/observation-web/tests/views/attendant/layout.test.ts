/**
 * Tests for the task graph layout: layers from edges, ordering, cycles, the empty graph, geometry.
 *
 * The layout is a pure function of task ids, dependency edges and a mode, so each rule is checked
 * on plain values: the layer is the longest chain of waits, the order inside a layer is by id
 * whatever order the input comes in, a cycle ends instead of looping, and nothing leaves the
 * viewBox.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/attendant/graph/layout.ts.
 *
 * Key invariants:
 *   - Two calls with the same input give deeply equal output (design.md 4.8).
 *
 * See Also:
 *   - tests/views/attendant/attendant_view.test.tsx for the drawing.
 */
import { describe, expect, it } from 'vitest';
import { layersOf, layoutGraph } from '../../../src/views/attendant/graph';
import type { GraphEdge, GraphLine } from '../../../src/views/attendant/graph';

const edge = (before: string, after: string): GraphEdge => ({ before, after });
const layerList = (ids: string[], edges: GraphEdge[]) => Object.fromEntries(layersOf(ids, edges));

describe('layers', () => {
  it('put a task after everything it waits on', () => {
    expect(layerList(['a', 'b', 'c'], [edge('a', 'b'), edge('b', 'c')])).toEqual({
      a: 0,
      b: 1,
      c: 2,
    });
  });

  it('use the longest chain, so a diamond joins below its longer side', () => {
    const edges = [edge('a', 'b'), edge('b', 'd'), edge('a', 'd'), edge('c', 'd')];
    expect(layerList(['a', 'b', 'c', 'd'], edges)).toEqual({ a: 0, b: 1, c: 0, d: 2 });
  });

  it('leave tasks with no edges in the first layer', () => {
    expect(layerList(['x', 'y'], [])).toEqual({ x: 0, y: 0 });
  });

  it('drop edges to unknown tasks, to itself and repeated ones', () => {
    const edges = [
      edge('a', 'ghost'),
      edge('ghost', 'a'),
      edge('a', 'a'),
      edge('a', 'b'),
      edge('a', 'b'),
    ];
    expect(layerList(['a', 'b'], edges)).toEqual({ a: 0, b: 1 });
  });

  it('end on a cycle, breaking it at the smallest id', () => {
    const layers = layerList(['a', 'b', 'c'], [edge('a', 'b'), edge('b', 'a'), edge('b', 'c')]);
    expect(layers).toEqual({ a: 0, b: 1, c: 2 });
    // A longer ring and a self-contained pair still place every task.
    const ring = layerList(['p', 'q', 'r'], [edge('p', 'q'), edge('q', 'r'), edge('r', 'p')]);
    expect(Object.keys(ring).sort()).toEqual(['p', 'q', 'r']);
  });
});

describe('layoutGraph', () => {
  const ids = ['task_3', 'task_1', 'task_2', 'task_4'];
  const edges = [edge('task_2', 'task_3'), edge('task_1', 'task_3')];

  it('is empty for an empty graph, in both modes', () => {
    for (const mode of ['wide', 'narrow'] as const) {
      const layout = layoutGraph([], [], mode);
      expect(layout).toMatchObject({ layers: 0, nodes: [], lines: [] });
      expect(layout.height).toBeGreaterThan(0);
    }
  });

  it('orders a layer by id whatever order the ids and edges arrive in', () => {
    const one = layoutGraph(ids, edges, 'wide');
    const two = layoutGraph([...ids].reverse(), [...edges].reverse(), 'wide');
    expect(two).toEqual(one);
    const first = one.nodes.filter((n) => n.layer === 0).sort((a, b) => a.y - b.y);
    expect(first.map((n) => n.id)).toEqual(['task_1', 'task_2', 'task_4']);
  });

  it('runs layers left to right when wide, with a line per dependency', () => {
    const layout = layoutGraph(ids, edges, 'wide');
    const at = (id: string) => layout.nodes.find((n) => n.id === id)!;
    expect(at('task_3').x).toBeGreaterThan(at('task_1').x + at('task_1').w);
    expect(at('task_1').y).toBeLessThan(at('task_2').y);
    expect(layout.lines.map((l) => l.key)).toEqual(['task_1>task_3', 'task_2>task_3']);
    expect(layout.layers).toBe(2);
  });

  it('runs layers top to bottom when narrow, two to a row, inside 360 units', () => {
    const many = Array.from({ length: 5 }, (_, i) => `t${i}`);
    const layout = layoutGraph([...many, 'z'], [edge('t0', 'z')], 'narrow');
    expect(layout.width).toBe(360);
    const rows = new Set(layout.nodes.filter((n) => n.layer === 0).map((n) => n.y));
    expect(rows.size).toBe(3);
    const z = layout.nodes.find((n) => n.id === 'z')!;
    expect(z.y).toBeGreaterThan(
      Math.max(...layout.nodes.filter((n) => n.layer === 0).map((n) => n.y)),
    );
  });

  // A line's axis-aligned segments, as point pairs.
  type Segment = [[number, number], [number, number]];
  const segmentsOf = (d: string): Segment[] => {
    const points = d
      .slice(1)
      .split(' L')
      .map((p) => p.split(' ').map(Number) as [number, number]);
    expect(points.length).toBeGreaterThanOrEqual(4);
    return points.slice(1).map((to, i) => [points[i]!, to]);
  };

  // The round 5 review's phone shot: layer 0 wraps task_4 under task_1 (two to a row at 420 px,
  // with the gap a lane wider for task_3's two lines in) and the line from task_1 to task_3 ran
  // straight through it. A segment must never run inside a node, in either mode, whether the
  // layer wraps, the line spans two layers or runs back up them.
  it.each([
    ['narrow', 420],
    ['narrow', 304],
    ['wide', null],
  ] as const)('never runs a line through a node (%s, %s px)', (mode, pane) => {
    const chain = [edge('task_1', 'task_5'), edge('task_3', 'task_5'), edge('task_5', 'task_1')];
    const layout = layoutGraph([...ids, 'task_5'], [...edges, ...chain], mode, pane);
    if (pane === 420) expect(new Set(layout.nodes.map((n) => n.x)).size).toBe(2);
    for (const line of layout.lines) {
      for (const [[x1, y1], [x2, y2]] of segmentsOf(line.d)) {
        expect(x1 === x2 || y1 === y2).toBe(true);
        for (const n of layout.nodes) {
          const inside =
            Math.min(x1, x2) < n.x + n.w &&
            Math.max(x1, x2) > n.x &&
            Math.min(y1, y2) < n.y + n.h &&
            Math.max(y1, y2) > n.y;
          expect(inside, `${line.key} through ${n.id}`).toBe(false);
        }
      }
    }
  });

  // The round 6 review's phone shot: with one task to a row, task_1's and task_2's lines into
  // task_3 took the same lane and the same entry, so the reader saw one line and one arrowhead.
  // Two lines into one task must share no collinear stretch and must end at different points, on
  // a phone and in the wide mode's lanes alike.
  it.each([
    ['narrow', 304],
    ['wide', null],
  ] as const)('gives each line into one task a lane and an entry of its own (%s)', (mode, pane) => {
    const chain = [edge('task_1', 'task_5'), edge('task_3', 'task_5'), edge('task_4', 'task_5')];
    const layout = layoutGraph([...ids, 'task_5'], [...edges, ...chain], mode, pane);
    const byTarget = new Map<string, GraphLine[]>();
    for (const line of layout.lines) {
      const target = line.key.split('>')[1]!;
      byTarget.set(target, [...(byTarget.get(target) ?? []), line]);
    }
    const overlap = (p: Segment, q: Segment): boolean => {
      const [axis, other] = p[0][0] === p[1][0] ? [0, 1] : [1, 0];
      if (q[0][axis] !== q[1][axis] || q[0][axis] !== p[0][axis]) return false;
      const span = (s: Segment): [number, number] => [
        Math.min(s[0][other]!, s[1][other]!),
        Math.max(s[0][other]!, s[1][other]!),
      ];
      const [pl, ph] = span(p);
      const [ql, qh] = span(q);
      return Math.min(ph, qh) - Math.max(pl, ql) > 0;
    };
    expect([...byTarget.values()].some((lines) => lines.length > 1)).toBe(true);
    for (const lines of byTarget.values()) {
      lines.forEach((one, i) => {
        for (const two of lines.slice(i + 1)) {
          expect(one.d.split(' L').at(-1), `${one.key} and ${two.key} end together`).not.toBe(
            two.d.split(' L').at(-1),
          );
          for (const p of segmentsOf(one.d)) {
            for (const q of segmentsOf(two.d)) {
              expect(overlap(p, q), `${one.key} and ${two.key} share a stretch`).toBe(false);
            }
          }
        }
      });
    }
  });

  it('sizes the narrow canvas from the pane and wraps to what fits', () => {
    const narrow = layoutGraph(ids, edges, 'narrow', 304);
    expect(narrow.width).toBe(304);
    expect(new Set(narrow.nodes.map((n) => n.x)).size).toBe(1);
    expect(layoutGraph(ids, edges, 'wide', 304).width).toBeGreaterThan(304);
  });

  it('keeps every node and line point inside the viewBox, as whole numbers', () => {
    for (const mode of ['wide', 'narrow'] as const) {
      const layout = layoutGraph(ids, [...edges, edge('task_3', 'task_1')], mode);
      for (const n of layout.nodes) {
        expect(Number.isInteger(n.x) && Number.isInteger(n.y)).toBe(true);
        expect(n.x).toBeGreaterThanOrEqual(0);
        expect(n.y).toBeGreaterThanOrEqual(0);
        expect(n.x + n.w).toBeLessThanOrEqual(layout.width);
        expect(n.y + n.h).toBeLessThanOrEqual(layout.height);
      }
      // A lane past the last row or column is inside the canvas too, as a whole number.
      for (const line of layout.lines) {
        for (const [[x, y]] of segmentsOf(line.d)) {
          expect(Number.isInteger(x) && Number.isInteger(y)).toBe(true);
          expect(x).toBeGreaterThanOrEqual(0);
          expect(y).toBeGreaterThanOrEqual(0);
          expect(x).toBeLessThanOrEqual(layout.width);
          expect(y).toBeLessThanOrEqual(layout.height);
        }
      }
    }
  });
});
