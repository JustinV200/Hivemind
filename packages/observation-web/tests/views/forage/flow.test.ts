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
  COLUMN_X,
  HEAD_H,
  layoutFlow,
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
    const edges = [COLUMN_X[0] + NODE_W, COLUMN_X[1] + NODE_W];
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

  it('marks a near-expiry grant in its label and its ribbon', () => {
    const layout = layoutFlow(
      flowModel(input({ grants: [grant({ id: 'g_a', expires_at: at(4) })] })),
    );
    expect(layout.boxes.find((b) => b.id === 'g_a')).toMatchObject({ near: true });
    expect(layout.boxes.find((b) => b.id === 'g_a')?.label).toContain('soon');
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
