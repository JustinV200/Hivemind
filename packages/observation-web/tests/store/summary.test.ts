/**
 * Tests for the summary reducer: folding frames into counters, staying bounded, and the seed.
 *
 * The strip's numbers are derived, not stored, so the tests feed frames and read the counters
 * back: a Cell is active or not by its lease or status, a bee leaves the count when it reports a
 * terminal state, the seed shows until the streams cover it, and no record grows without bound.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/store/summary.ts.
 *
 * Key invariants:
 *   - Reducers are pure: each test checks that the input slice was not changed.
 *
 * See Also:
 *   - docs/observation/design.md section 2 for what the strip counts.
 */
import { describe, expect, it } from 'vitest';
import { initialSummary, MAX_BEES, reduceSummary, summaryCounters } from '../../src/store/summary';
import type { SummaryEvent, SummarySlice } from '../../src/store/summary';
import { cellFrame, episodeFrame, SEED, telemetryFrame } from '../support/frames';

const fold = (events: SummaryEvent[], from: SummarySlice = initialSummary): SummarySlice =>
  events.reduce(reduceSummary, from);

describe('cells', () => {
  it('counts active and inactive, Real and Virtual', () => {
    const slice = fold([
      {
        type: 'cell',
        frame: cellFrame({ id: 'a', kind: 'REAL', lease_state: 'OPEN', virtual_status: null }),
      },
      {
        type: 'cell',
        frame: cellFrame({ id: 'b', kind: 'REAL', lease_state: 'RELEASED', virtual_status: null }),
      },
      { type: 'cell', frame: cellFrame({ id: 'c', kind: 'VIRTUAL', virtual_status: 'GRANTED' }) },
      { type: 'cell', frame: cellFrame({ id: 'd', kind: 'VIRTUAL', virtual_status: 'DESTROYED' }) },
    ]);
    expect(summaryCounters(slice)).toMatchObject({
      cells_active: 2,
      cells_inactive: 2,
      cells_real: 2,
      cells_virtual: 2,
    });
  });

  it('keeps one entry per Cell: a later frame replaces the earlier one', () => {
    const slice = fold([
      { type: 'cell', frame: cellFrame({ id: 'a', virtual_status: 'PROVISIONING' }) },
      { type: 'cell', frame: cellFrame({ id: 'a', virtual_status: 'READY' }) },
    ]);
    expect(Object.keys(slice.cells)).toEqual(['a']);
    expect(summaryCounters(slice)).toMatchObject({ cells_active: 1, cells_inactive: 0 });
  });

  it('returns the same slice for a frame that changes nothing', () => {
    const slice = fold([{ type: 'cell', frame: cellFrame({ id: 'a' }) }]);
    expect(reduceSummary(slice, { type: 'cell', frame: cellFrame({ id: 'a' }) })).toBe(slice);
  });
});

describe('bees', () => {
  it('counts a Worker by its id and a Warden by its own when no Worker is named', () => {
    const slice = fold([
      { type: 'telemetry', frame: telemetryFrame({ warden_id: 'w1' }) },
      {
        type: 'telemetry',
        frame: telemetryFrame({ warden_id: 'w1', worker_id: 'k1', worker_state: 'RUNNING' }),
      },
      {
        type: 'telemetry',
        frame: telemetryFrame({ warden_id: 'w1', worker_id: 'k1', worker_state: 'RUNNING' }),
      },
    ]);
    expect(summaryCounters(slice)?.active_bees).toBe(2);
  });

  it('drops a bee that reports a terminal state', () => {
    const running = telemetryFrame({ worker_id: 'k1', worker_state: 'RUNNING' });
    const done = telemetryFrame({ worker_id: 'k1', worker_state: 'DONE' });
    const slice = fold([
      { type: 'telemetry', frame: running },
      { type: 'telemetry', frame: telemetryFrame({ warden_id: 'w9', warden_state: 'OFFLINE' }) },
      { type: 'telemetry', frame: done },
    ]);
    expect(summaryCounters(slice)?.active_bees).toBe(0);
  });

  it('stays bounded however many bees report', () => {
    const events: SummaryEvent[] = Array.from({ length: MAX_BEES + 500 }, (_, i) => ({
      type: 'telemetry',
      frame: telemetryFrame({ worker_id: `k${i}`, worker_state: 'RUNNING' }),
    }));
    const slice = fold(events);
    expect(Object.keys(slice.bees)).toHaveLength(MAX_BEES);
    // The newest bees survive; the oldest were dropped.
    expect(slice.bees[`k${MAX_BEES + 499}`]).toBeDefined();
    expect(slice.bees.k0).toBeUndefined();
  });
});

describe('thinking', () => {
  it('splits principals by whether their latest episode was autopilot', () => {
    const slice = fold([
      { type: 'episode', frame: episodeFrame({ principal: 'queen', is_autopilot: false }) },
      { type: 'episode', frame: episodeFrame({ principal: 'w1', is_autopilot: true }) },
      { type: 'episode', frame: episodeFrame({ principal: 'w2', is_autopilot: true }) },
      // The latest episode wins: w2 woke up.
      { type: 'episode', frame: episodeFrame({ principal: 'w2', is_autopilot: false }) },
    ]);
    expect(summaryCounters(slice)).toMatchObject({ bees_awake: 2, bees_autopilot: 1 });
  });

  it('forgets a principal when its bee ends', () => {
    const slice = fold([
      { type: 'telemetry', frame: telemetryFrame({ worker_id: 'k1', worker_state: 'RUNNING' }) },
      { type: 'episode', frame: episodeFrame({ principal: 'k1', is_autopilot: false }) },
      { type: 'telemetry', frame: telemetryFrame({ worker_id: 'k1', worker_state: 'KILLED' }) },
    ]);
    expect(slice.autopilot).toEqual({});
  });
});

describe('seed', () => {
  it('shows nothing until there is something to show', () => {
    expect(summaryCounters(initialSummary)).toBeNull();
  });

  it('shows the seed until the streams cover it, then the streams', () => {
    let slice = fold([{ type: 'seed', counters: SEED }]);
    expect(summaryCounters(slice)).toEqual(SEED);
    // One Cell streamed of five counted: still the seed's numbers, not a partial count.
    slice = fold([{ type: 'cell', frame: cellFrame({ id: 'a' }) }], slice);
    expect(summaryCounters(slice)?.cells_active).toBe(3);
    // All five have streamed: the streams take over for good.
    slice = fold(
      ['b', 'c', 'd', 'e'].map((id) => ({
        type: 'cell' as const,
        frame: cellFrame({ id, virtual_status: 'DESTROYED' }),
      })),
      slice,
    );
    expect(summaryCounters(slice)).toMatchObject({ cells_active: 1, cells_inactive: 4 });
  });

  it('does not fall back to the seed when a bee later finishes', () => {
    const running = (id: string): SummaryEvent => ({
      type: 'telemetry',
      frame: telemetryFrame({ worker_id: id, worker_state: 'RUNNING' }),
    });
    let slice = fold([
      { type: 'seed', counters: { ...SEED, active_bees: 2 } },
      running('a'),
      running('b'),
    ]);
    expect(summaryCounters(slice)?.active_bees).toBe(2);
    slice = fold(
      [{ type: 'telemetry', frame: telemetryFrame({ worker_id: 'a', worker_state: 'DONE' }) }],
      slice,
    );
    expect(summaryCounters(slice)?.active_bees).toBe(1);
  });

  it('works with streams alone when the seed never arrives', () => {
    const slice = fold([{ type: 'cell', frame: cellFrame({ id: 'a' }) }]);
    expect(summaryCounters(slice)).toMatchObject({ cells_active: 1, active_bees: 0 });
  });

  it('does not change its input', () => {
    const before = fold([{ type: 'cell', frame: cellFrame({ id: 'a' }) }]);
    const snapshot = JSON.stringify(before);
    reduceSummary(before, { type: 'cell', frame: cellFrame({ id: 'z' }) });
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});
