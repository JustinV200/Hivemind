/**
 * Tests for the Cost slice: the read, the fold of a completed call, and what is ignored.
 *
 * The reducer is pure, so these drive it with events and look at the state: that a read replaces
 * the figures, that an `llm.call` frame with usage adds into its goal, slot and provider (and into
 * the totals and the local saving), that a call without a goal lands under the null goal, that a
 * frame without usage or of another kind changes nothing, and that a replayed call counts once.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/cost/slice/.
 *
 * Key invariants:
 *   - A frame that changes nothing returns the very same state object.
 *
 * See Also:
 *   - tests/views/cost/cost_view.test.tsx for the throttled re-read, which lives in the hook.
 */
import { describe, expect, it } from 'vitest';
import { initialCost, MAX_SEEN, reduceCost } from '../../../src/views/cost/slice';
import type { CostEvent, CostState } from '../../../src/views/cost/slice';
import { callEvent, costFrom, line, sampleCost, trailFrame } from './support';

const frame = (over: Parameters<typeof callEvent>[0] = {}): CostEvent => ({
  type: 'frame',
  frame: trailFrame(callEvent(over)),
});

const seeded = (): CostState => reduceCost(initialCost, { type: 'read', cost: sampleCost() });

const goal = (state: CostState, id: string | null) =>
  state.cost.goals.find((g) => g.goal_id === id);

describe('the read', () => {
  it('seeds the figures and clears an earlier error', () => {
    const failed = reduceCost(initialCost, { type: 'failed', error: new Error('x') });
    const state = reduceCost(failed, { type: 'read', cost: sampleCost() });
    expect(state.seeded).toBe(true);
    expect(state.error).toBeNull();
    expect(state.cost.total_usd).toBeCloseTo(0.047, 6);
    expect(state.cost.goals.map((g) => g.goal_id)).toEqual(['goal_1', 'goal_2', null]);
  });

  it('replaces what frames folded, so a call is never counted twice', () => {
    const folded = reduceCost(seeded(), frame({ payload: { goal_id: 'goal_1' } }));
    const reread = reduceCost(folded, { type: 'read', cost: sampleCost() });
    expect(reread.cost).toEqual(sampleCost());
  });

  it('opens clean', () => {
    expect(reduceCost(seeded(), { type: 'open' })).toBe(initialCost);
    expect(reduceCost(initialCost, { type: 'open' })).toBe(initialCost);
  });
});

describe('an llm.call frame with usage', () => {
  it('folds into the goal, slot and provider it names, and into the totals', () => {
    const state = reduceCost(
      seeded(),
      frame({
        slot: 'QUEEN',
        provider: 'cloud',
        payload: { goal_id: 'goal_1' },
        usage: { cached_tokens: 7, cost_usd: 0.02, input_tokens: 500, output_tokens: 100 },
      }),
    );
    const g1 = goal(state, 'goal_1')!;
    expect(g1.cost_usd).toBeCloseTo(0.05, 6);
    expect(g1.calls).toBe(6);
    const queen = g1.lines.find((l) => l.slot === 'QUEEN' && l.provider === 'cloud')!;
    expect(queen).toMatchObject({
      calls: 3,
      input_tokens: 1500,
      output_tokens: 300,
      cached_tokens: 7,
    });
    expect(queen.cost_usd).toBeCloseTo(0.05, 6);
    const slot = state.cost.slots.find((s) => s.slot === 'QUEEN')!;
    expect(slot.cost_usd).toBeCloseTo(0.055, 6);
    expect(slot.hosted_usd).toBeCloseTo(0.055, 6);
    expect(state.cost.providers.find((p) => p.provider === 'cloud')).toMatchObject({ calls: 5 });
    expect(state.cost.total_usd).toBeCloseTo(0.067, 6);
    expect(state.cost.if_local_saving_usd).toBeCloseTo(0.047 + 0.02, 6);
    expect(state.ticks).toBe(1);
  });

  it('starts a new goal, slot, provider and line when none matched, keeping the order', () => {
    const state = reduceCost(
      seeded(),
      frame({
        slot: 'SCOUT',
        provider: 'edge',
        payload: { goal_id: 'goal_3' },
        usage: { cached_tokens: 0, cost_usd: 0.9, input_tokens: 10, output_tokens: 5 },
      }),
    );
    // goal_3 is the costliest now; the null goal still comes after goals that cost more.
    expect(state.cost.goals.map((g) => g.goal_id)).toEqual(['goal_3', 'goal_1', 'goal_2', null]);
    expect(state.cost.slots.map((s) => s.slot)).toEqual(['QUEEN', 'SCOUT', 'WORKER']);
    expect(state.cost.providers.map((p) => p.provider)).toEqual(['edge', 'cloud', 'local']);
    expect(goal(state, 'goal_3')!.lines).toHaveLength(1);
  });

  it('puts a call that names no goal under the null goal row', () => {
    const state = reduceCost(seeded(), frame({ payload: { grant_id: 'grant_1' } }));
    const unattributed = goal(state, null)!;
    expect(unattributed.calls).toBe(2);
    expect(unattributed.cost_usd).toBeCloseTo(0.025, 6);
    // Costliest first, so the null goal sorts among the others; only equals put it last.
    expect(state.cost.goals.map((g) => g.goal_id)).toEqual(['goal_1', null, 'goal_2']);
  });

  it('creates the null goal from an empty snapshot, before the read lands', () => {
    const state = reduceCost(initialCost, frame());
    expect(state.seeded).toBe(false);
    expect(state.cost.goals).toEqual([
      expect.objectContaining({ goal_id: null, calls: 1, cost_usd: 0.02 }),
    ]);
  });

  it('counts a free local call as spend of zero and adds nothing to the saving', () => {
    const state = reduceCost(
      seeded(),
      frame({
        slot: 'WORKER',
        provider: 'local',
        payload: { goal_id: 'goal_1' },
        usage: { cached_tokens: 0, cost_usd: 0, input_tokens: 90, output_tokens: 10 },
      }),
    );
    expect(state.cost.total_usd).toBeCloseTo(0.047, 6);
    expect(state.cost.if_local_saving_usd).toBeCloseTo(0.047, 6);
    expect(state.cost.providers.find((p) => p.provider === 'local')?.calls).toBe(4);
    expect(state.ticks).toBe(1);
  });

  it('agrees with the Entrance when the same call is in a read', () => {
    const base = [line({ goal_id: 'goal_1', slot: 'QUEEN', provider: 'cloud', cost_usd: 0.01 })];
    const extra = line({ goal_id: 'goal_1', slot: 'QUEEN', provider: 'cloud', cost_usd: 0.02 });
    const folded = reduceCost(
      reduceCost(initialCost, { type: 'read', cost: costFrom(base) }),
      frame({
        payload: { goal_id: 'goal_1' },
        usage: { cached_tokens: 0, cost_usd: 0.02, input_tokens: 1000, output_tokens: 200 },
      }),
    );
    expect(folded.cost.goals[0]?.cost_usd).toBeCloseTo(
      costFrom([...base, extra]).goals[0]!.cost_usd,
      9,
    );
    expect(folded.cost.goals[0]?.lines[0]).toMatchObject({ calls: 2, input_tokens: 2000 });
  });
});

describe('frames that change nothing', () => {
  it('ignores an llm.call without usage', () => {
    const state = seeded();
    expect(reduceCost(state, frame({ usage: null }))).toBe(state);
    expect(reduceCost(state, frame({ usage: undefined }))).toBe(state);
  });

  it('ignores a call that names no slot or no provider, and other kinds of event', () => {
    const state = seeded();
    expect(reduceCost(state, frame({ slot: null }))).toBe(state);
    expect(reduceCost(state, frame({ provider: null }))).toBe(state);
    expect(reduceCost(state, frame({ kind: 'llm.error' }))).toBe(state);
    expect(reduceCost(state, frame({ kind: 'task.assigned', family: 'task' }))).toBe(state);
  });

  it('counts a replayed call once', () => {
    const once = reduceCost(seeded(), frame({ id: 'ev_9' }));
    expect(reduceCost(once, frame({ id: 'ev_9' }))).toBe(once);
    expect(reduceCost(once, frame({ id: 'ev_10' })).ticks).toBe(2);
  });

  it('remembers only the newest ids', () => {
    let state = seeded();
    for (let i = 0; i < MAX_SEEN + 5; i++) state = reduceCost(state, frame({ id: `ev_${i}` }));
    expect(state.seen).toHaveLength(MAX_SEEN);
    expect(state.seen.at(-1)).toBe(`ev_${MAX_SEEN + 4}`);
  });
});

describe('a failed read', () => {
  it('keeps the figures and records the error', () => {
    const error = new Error('x');
    const state = reduceCost(seeded(), { type: 'failed', error });
    expect(state.error).toBe(error);
    expect(state.cost.goals).toHaveLength(3);
  });
});
