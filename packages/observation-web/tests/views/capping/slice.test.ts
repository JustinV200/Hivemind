/**
 * Tests for the Capping slice: the seed, folding `capping.*` frames at once, the bound and the counts.
 *
 * The reducer is pure, so these run on states and events with no store and no network. A hop is
 * folded the way the server would on a re-read: the proposal moves to the state its kind names, a
 * capped or rejected hop adds a verdict, a rolled-back hop marks the rollback, and an audit adds a
 * finding and moves the tier's rates.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/capping/slice/.
 *
 * Key invariants:
 *   - A frame of another family, or an old or repeated hop, returns the very state it was given.
 *   - Every `capping.*` frame bumps `ticks` (the cue to re-read), folded or not.
 *
 * See Also:
 *   - tests/views/capping/capping_view.test.tsx for the view and the throttled re-read.
 */
import { describe, expect, it } from 'vitest';
import { initialCapping, MAX_PROPOSALS, reduceCapping } from '../../../src/views/capping/slice';
import type { CappingState } from '../../../src/views/capping/slice';
import { busyView, countRow, emptyView, hop, proposalRow, rateRow } from './support';

const seededState = (view = busyView()): CappingState =>
  reduceCapping(initialCapping, { type: 'seeded', view });

const fold = (state: CappingState, kind: string, subject: string, over = {}): CappingState =>
  reduceCapping(state, { type: 'trail', frame: hop(kind, subject, over) });

describe('the seed', () => {
  it('joins the three row lists by id, newest first, and keeps the counts and rates', () => {
    const state = seededState();
    expect(state.seeded).toBe(true);
    expect(state.proposals.map((p) => p.id)).toEqual(['prop_roll', 'prop_rej', 'prop_wait']);
    expect(state.verdicts).toHaveLength(2);
    expect(state.counts).toHaveLength(3);
    expect(state.rates.map((r) => r.tier)).toEqual(['IRREVERSIBLE']);
    expect(state.error).toBeNull();
  });

  it('clears an earlier refusal, and a failed read records one', () => {
    const failed = reduceCapping(initialCapping, { type: 'failed', error: new Error('no') });
    expect(failed.error).toBeInstanceOf(Error);
    expect(reduceCapping(failed, { type: 'seeded', view: emptyView() }).error).toBeNull();
  });

  it('keeps at most MAX_PROPOSALS rows, the newest', () => {
    const many = Array.from({ length: MAX_PROPOSALS + 20 }, (_, i) =>
      proposalRow({
        id: `p${String(i).padStart(4, '0')}`,
        updated_at: new Date(Date.UTC(2026, 0, 1, 0, 0, i)).toISOString(),
      }),
    );
    const state = seededState(emptyView({ in_flight: many }));
    expect(state.proposals).toHaveLength(MAX_PROPOSALS);
    expect(state.proposals[0]?.id).toBe(`p${String(MAX_PROPOSALS + 19).padStart(4, '0')}`);
  });
});

describe('a capping frame', () => {
  it('moves a proposal to the state its kind names and moves the counts with it', () => {
    const state = fold(seededState(), 'capping.capped', 'prop_wait', {
      payload: { tier: 'SCRATCH_WRITE', checks_passed: 3 },
    });
    expect(state.proposals.find((p) => p.id === 'prop_wait')?.state).toBe('CAPPED');
    expect(state.proposals[0]?.id).toBe('prop_wait');
    expect(state.counts).toContainEqual(countRow('SCRATCH_WRITE', 'CAPPED', 1));
    expect(state.counts.some((c) => c.state === 'CHECKING')).toBe(false);
    expect(state.ticks).toBe(1);
  });

  it('adds a verdict when the gate speaks, with the failing check and the judge flag', () => {
    const state = fold(seededState(), 'capping.rejected', 'prop_wait', {
      payload: { failing_check: 'judge', judge_error: true, checks_run: 2 },
    });
    expect(state.verdicts[0]).toMatchObject({
      proposal_id: 'prop_wait',
      verdict: 'REJECTED',
      failing_check: 'judge',
      judge_error: true,
      checks_run: 2,
      bee_id: 'worker_1',
    });
    const row = state.proposals.find((p) => p.id === 'prop_wait');
    expect(row).toMatchObject({ state: 'REJECTED', failing_check: 'judge' });
    // A finished proposal also counts toward its tier's finished work.
    expect(state.rates.find((r) => r.tier === 'SCRATCH_WRITE')?.finished).toBe(1);
  });

  it('marks a rollback with how the Cell was restored', () => {
    const base = fold(seededState(), 'capping.applied', 'prop_wait', {
      payload: { action_kind: 'GUI' },
    });
    const state = fold(base, 'capping.rolled_back', 'prop_wait', {
      at: '2026-01-01T00:02:00Z',
      payload: { method: 'REVERSE_DIFF' },
    });
    expect(state.proposals.find((p) => p.id === 'prop_wait')).toMatchObject({
      state: 'ROLLED_BACK',
      rollback_method: 'REVERSE_DIFF',
      action_kind: 'GUI',
    });
  });

  it('opens a row for a proposal first seen on a later hop, from the event', () => {
    const state = fold(seededState(), 'capping.proposed', 'prop_new', {
      actor: 'worker_9',
      payload: { tier: 'SPEND', task_id: 'task_9', cell_id: 'cell_9' },
    });
    expect(state.proposals[0]).toMatchObject({
      id: 'prop_new',
      state: 'PROPOSED',
      bee_id: 'worker_9',
      tier: 'SPEND',
      task_id: 'task_9',
      cell_id: 'cell_9',
    });
    expect(state.counts).toContainEqual(countRow('SPEND', 'PROPOSED', 1));
  });

  it('adds a sampled audit and moves the tier rates by the server formulas', () => {
    const start = seededState(
      emptyView({
        in_flight: [],
        finished: [
          proposalRow({ state: 'VERIFIED' }),
          proposalRow({ id: 'prop_2', state: 'VERIFIED' }),
        ],
        audit_rates: [rateRow({ finished: 2 })],
      }),
    );
    const state = fold(start, 'capping.audited', 'prop_1', {
      payload: { tier: 'SCRATCH_WRITE', outcome: 'REJECT' },
    });
    expect(state.audits[0]).toMatchObject({
      proposal_id: 'prop_1',
      outcome: 'REJECT',
      bee_id: 'worker_1',
    });
    expect(state.rates[0]).toMatchObject({
      audited: 1,
      rejected: 1,
      sampled_fraction: 0.5,
      rejection_fraction: 1,
    });
    // A judge that never answered is unscored: counted as audited, not as a finding.
    const silent = fold(state, 'capping.audited', 'prop_2', {
      at: '2026-01-01T00:03:00Z',
      payload: { tier: 'SCRATCH_WRITE', judge_error: true },
    });
    expect(silent.rates[0]).toMatchObject({ audited: 2, inconclusive: 1, rejection_fraction: 1 });
  });

  it('ignores an old hop and a repeated one, returning the same state', () => {
    const moved = fold(seededState(), 'capping.capped', 'prop_wait', {
      at: '2026-01-01T00:01:00Z',
    });
    const again = reduceCapping(moved, {
      type: 'trail',
      frame: hop('capping.capped', 'prop_wait', { at: '2026-01-01T00:01:00Z' }),
    });
    const older = fold({ ...moved, ticks: 0 }, 'capping.checked', 'prop_wait', {
      at: '2026-01-01T00:00:05Z',
    });
    expect(older.proposals).toBe(moved.proposals);
    expect(older.counts).toBe(moved.counts);
    expect(again.proposals).toBe(moved.proposals);
    expect(again.verdicts).toBe(moved.verdicts);
  });
});

describe('a frame that is not for this view', () => {
  it('returns the very state it was given for another family', () => {
    const state = seededState();
    const next = reduceCapping(state, {
      type: 'trail',
      frame: hop('alarm.raised', 'cell_1', { family: 'alarm' }),
    });
    expect(next).toBe(state);
  });

  it('only counts a capping frame that comes before the seed', () => {
    const next = fold(initialCapping, 'capping.proposed', 'prop_x');
    expect(next.proposals).toHaveLength(0);
    expect(next.ticks).toBe(1);
  });

  it('counts but does not fold an unknown capping kind', () => {
    const state = seededState();
    const next = fold(state, 'capping.summary', 'prop_wait');
    expect(next.proposals).toBe(state.proposals);
    expect(next.ticks).toBe(1);
  });

  it('forgets earlier ticks when the view opens again, and keeps its rows', () => {
    const state = fold(seededState(), 'capping.capped', 'prop_wait');
    const reopened = reduceCapping(state, { type: 'open' });
    expect(reopened.ticks).toBe(0);
    expect(reopened.proposals).toBe(state.proposals);
  });
});
