/**
 * Tests for the Forage flow model and layout: pool to Wardens to grants, in four units.
 *
 * The model and the layout are pure, so every case is numbers: which figures each unit uses, where
 * the schema has no figure (a pool for spend, a use for seats), how box heights follow amounts on
 * one scale, that ribbons join each Warden to its own grants, how a grant nearing expiry is
 * flagged, and that the same figures lay out and render identically twice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/forage/flow/.
 *
 * Key invariants:
 *   - "Now" is fixed in each test, so the ten-minute window is exact.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.7 and 4.8.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ForageFlow } from '../../../src/views/forage/flow/diagram';
import {
  FLOW_WIDTH,
  HEAD_H,
  layoutFlow,
  LINE_H,
  MIN_BOX,
  NODE_W,
  PAD,
} from '../../../src/views/forage/flow/layout';
import { flowModel } from '../../../src/views/forage/flow/model';
import type { FlowInput } from '../../../src/views/forage/flow/model';
import { grant, headroom, reserve, warden } from './support';

const NOW = Date.parse('2026-01-01T00:00:00Z');
const at = (minutes: number): string => new Date(NOW + minutes * 60_000).toISOString();

const input = (over: Partial<FlowInput> = {}): FlowInput => ({
  unit: 'seats',
  grants: [
    grant({ id: 'g_a', holder: 'w_1', cell_id: 'c_1', seats: 4, expires_at: at(60) }),
    grant({ id: 'g_b', holder: 'w_1', cell_id: 'c_1', seats: 2, expires_at: at(60) }),
    grant({ id: 'g_c', holder: 'w_2', cell_id: 'c_2', seats: 6, expires_at: at(60) }),
  ],
  headroom: headroom({ shared_seats: 8, sub_bees: 3 }),
  reserve: reserve({ seats: 2 }),
  wardens: [warden({ id: 'w_1' })],
  nowMs: NOW,
  ...over,
});

describe('the model', () => {
  it('groups grants under their Warden, Wardens by Cell id', () => {
    const model = flowModel(input());
    expect(model.wardens.map((w) => [w.id, w.amount, w.grants.map((g) => g.id)])).toEqual([
      ['w_1', 6, ['g_a', 'g_b']],
      ['w_2', 6, ['g_c']],
    ]);
    expect([model.reserve, model.granted, model.free]).toEqual([2, 12, 8]);
  });

  it('uses spend and tokens with a use figure, and no pool figure', () => {
    const spend = flowModel(
      input({ unit: 'spend', grants: [grant({ spend_budget: 5, spent: 1.25 })] }),
    );
    expect(spend.wardens[0]?.grants[0]).toMatchObject({ amount: 5, used: 1.25 });
    expect(spend.free).toBeNull();
    expect(spend.reserve).toBe(0);
    const tokens = flowModel(
      input({ unit: 'tokens', grants: [grant({ token_budget: 900, tokens_spent: 90 })] }),
    );
    expect(tokens.wardens[0]?.used).toBe(90);
  });

  it('has no use figure for seats or sub-bees, and a pool figure for each', () => {
    const bees = flowModel(input({ unit: 'sub_bees' }));
    expect(bees.free).toBe(3);
    expect(bees.wardens[0]?.used).toBeNull();
    expect(bees.wardens[0]?.grants[0]?.used).toBeNull();
  });

  it('flags a grant within ten minutes of expiry, boundary included', () => {
    const model = flowModel(
      input({
        grants: [
          grant({ id: 'g_a', expires_at: at(10) }),
          grant({ id: 'g_b', expires_at: at(11) }),
        ],
      }),
    );
    expect(model.wardens[0]?.grants.map((g) => g.near)).toEqual([true, false]);
  });
});

describe('the layout', () => {
  it('sizes boxes on one scale and stacks them with gaps', () => {
    const layout = layoutFlow(flowModel(input()));
    const box = (id: string) => layout.boxes.find((b) => b.id === id)!;
    // Pool 2 + 12 + 8 = 22 is the tallest column, so 200 / 22 per unit.
    expect(layout.scale).toBeCloseTo(200 / 22, 6);
    expect(box('pool-reserve').h).toBeCloseTo(2 * (200 / 22), 1);
    expect(box('pool-granted').h).toBeCloseTo(12 * (200 / 22), 1);
    expect(box('pool-granted').y).toBeCloseTo(PAD + HEAD_H + box('pool-reserve').h + 6, 1);
    expect(box('w_1').h).toBeCloseTo(6 * (200 / 22), 1);
    expect(box('g_c').column).toBe(2);
    expect(layout.height).toBeGreaterThan(200);
  });

  it('joins the granted pool to each Warden and each Warden to its own grants only', () => {
    const layout = layoutFlow(flowModel(input()));
    expect(layout.ribbons.map((r) => r.id)).toEqual([
      'pool-granted>w_1',
      'pool-granted>w_2',
      'w_1>g_a',
      'w_1>g_b',
      'w_2>g_c',
    ]);
    // Every ribbon leaves the right edge of a box in the pool or the Warden column.
    const edges = [layout.columns[0] + NODE_W, layout.columns[1] + NODE_W];
    const startOf = (path: string): number => Number(path.split(' ')[0]!.slice(1));
    expect(layout.ribbons.every((r) => edges.includes(startOf(r.path)))).toBe(true);
  });

  it('fills a box from the bottom by what was used', () => {
    const layout = layoutFlow(
      flowModel(
        input({ unit: 'spend', grants: [grant({ id: 'g_a', spend_budget: 10, spent: 2.5 })] }),
      ),
    );
    const box = layout.boxes.find((b) => b.id === 'g_a')!;
    expect(box.usedH).toBeCloseTo(box.h * 0.25, 1);
  });

  // The word "soon" goes under the task's name, which keeps its tail (the part that tells ids
  // apart) on the wide canvas and is the tail alone where a phone's eight characters hold no more.
  it.each([
    [null, ['task_…9K4L1', 'soon']],
    [304, ['…9K4L1', 'soon']],
  ])('marks a near-expiry grant in its label and its ribbon (%s px pane)', (pane, lines) => {
    const near = grant({
      id: 'g_a',
      task_id: 'task_01HZK5B2A7C4DF8G1H3JE9K4L1',
      expires_at: at(4),
    });
    const layout = layoutFlow(flowModel(input({ grants: [near] })), pane);
    expect(layout.boxes.find((b) => b.id === 'g_a')).toMatchObject({ near: true, lines });
    expect(layout.ribbons.some((r) => r.near)).toBe(true);
  });

  it('gives every box the minimum height when nothing has an amount', () => {
    const layout = layoutFlow(
      flowModel(
        input({
          grants: [grant({ seats: 0 })],
          reserve: reserve({ seats: 0 }),
          headroom: headroom({ shared_seats: 0 }),
        }),
      ),
    );
    expect(layout.scale).toBe(0);
    expect(layout.boxes.every((b) => b.h === MIN_BOX)).toBe(true);
    expect(layout.ribbons.every((r) => !r.path.includes('NaN'))).toBe(true);
  });

  it('is empty, and finite, with no grants', () => {
    const layout = layoutFlow(flowModel(input({ grants: [], headroom: null, reserve: null })));
    expect(layout.boxes).toEqual([]);
    expect(Number.isFinite(layout.height)).toBe(true);
  });

  // The canvas is the pane's width on a phone, so 12 unit text is 12 px; labels move left of
  // their boxes, stack the pool's amount under its word (the box growing to two lines) rather
  // than cut it, keep fewer characters per line, and nothing is drawn past the canvas.
  it.each([
    [null, FLOW_WIDTH, [90, 232, 374], false, ['Free 8']],
    [700, FLOW_WIDTH, [90, 232, 374], false, ['Free 8']],
    [304, 304, [57, 166, 275], true, ['Free', '8']],
    [200, 240, [37, 126, 215], true, ['Free', '8']],
  ])('draws on a canvas sized from a %s px pane', (pane, width, columns, left, free) => {
    const layout = layoutFlow(flowModel(input()), pane);
    expect(layout.width).toBe(width);
    expect(layout.columns).toEqual(columns);
    expect(layout.labelLeft).toEqual([true, left, left]);
    expect(layout.boxes.every((b) => b.x + b.w <= width)).toBe(true);
    const longest = Math.max(...layout.boxes.flatMap((b) => b.lines.map((l) => l.length)));
    expect(longest).toBeLessThanOrEqual(pane === 200 ? 5 : pane === 304 ? 8 : 13);
    const pool = layout.boxes.find((b) => b.id === 'pool-free')!;
    expect(pool.lines).toEqual(free);
    expect(pool.h).toBeGreaterThanOrEqual(LINE_H * free.length);
  });
});

describe('the diagram', () => {
  it('renders identically twice, with tokens only and a title on every box', () => {
    const model = flowModel(input());
    const draw = (): string => renderToStaticMarkup(createElement(ForageFlow, { model }));
    expect(draw()).toBe(draw());
    const html = draw();
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(html).toContain('aria-labelledby="forage-flow-title"');
    expect(html).toContain('Warden w_1 on c_1');
    expect((html.match(/<title>/g) ?? []).length).toBeGreaterThanOrEqual(
      layoutFlow(model).boxes.length,
    );
  });
});
