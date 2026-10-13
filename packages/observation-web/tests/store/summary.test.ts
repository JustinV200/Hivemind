/**
 * Tests for the summary reducer: folding frames into counters, staying bounded, and the seed.
 *
 * The strip's numbers are derived, not stored, so the tests feed frames and read the counters
 * back: a Cell is active or not by its Warden's mode, a bee leaves the count when it reports a
 * terminal state or its Warden's next Heartbeat no longer lists it, the seed shows until the streams cover it, and no record grows without bound.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/store/slices/summary.ts.
 *
 * Key invariants:
 *   - Reducers are pure: each test checks that the input slice was not changed.
 *
 * See Also:
 *   - docs/observation/design.md section 2 for what the strip counts.
 */
import { describe, expect, it } from 'vitest';
import {
  initialSummary,
  MAX_BEES,
  reduceSummary,
  summaryCounters,
} from '../../src/store/slices/summary';
import type { SummaryEvent, SummarySlice } from '../../src/store/slices/summary';
import { cellFrame, episodeFrame, SEED, SEED_STRIP, telemetryFrame } from '../support/frames';

const fold = (events: SummaryEvent[], from: SummarySlice = initialSummary): SummarySlice =>
  events.reduce(reduceSummary, from);

describe('cells', () => {
  it('counts active and inactive, Real and Virtual', () => {
    const slice = fold([
      {
        type: 'cell',
        frame: cellFrame({ id: 'a', kind: 'REAL', mode: 'ACTIVE', virtual_status: null }),
      },
      {
        type: 'cell',
        frame: cellFrame({ id: 'b', kind: 'REAL', mode: 'WATCH', virtual_status: null }),
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

  it('follows the Warden and not the lease: a leased Cell in WATCH is inactive', () => {
    const slice = fold([
      {
        type: 'cell',
        frame: cellFrame({
          id: 'a',
          kind: 'REAL',
          lease_state: 'OPEN',
          mode: 'WATCH',
          virtual_status: null,
        }),
      },
      {
        type: 'cell',
        frame: cellFrame({
          id: 'b',
          kind: 'REAL',
          lease_state: 'RELEASED',
          mode: 'ACTIVE',
          virtual_status: null,
        }),
      },
      // No Warden mode yet, and a torn-down Virtual Cell whose Warden edge is stale: both idle.
      {
        type: 'cell',
        frame: cellFrame({ id: 'c', kind: 'REAL', mode: null, virtual_status: null }),
      },
      { type: 'cell', frame: cellFrame({ id: 'd', virtual_status: 'DESTROYED', mode: 'ACTIVE' }) },
    ]);
    expect(summaryCounters(slice)).toMatchObject({ cells_active: 1, cells_inactive: 3 });
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

  it('drops a sub-bee its Warden no longer lists, though no terminal sample arrives', () => {
    const beat = (at: string, bees: string[]): SummaryEvent[] => [
      { type: 'telemetry', frame: telemetryFrame({ warden_id: 'w1', at, sub_bees: bees.length }) },
      ...bees.map((id) => ({
        type: 'telemetry' as const,
        frame: telemetryFrame({ warden_id: 'w1', worker_id: id, worker_state: 'RUNNING', at }),
      })),
    ];
    let slice = fold(beat('2026-01-01T00:00:01Z', ['k1', 'k2']));
    expect(summaryCounters(slice)?.active_bees).toBe(3);
    // The next Heartbeat lists k2 only: k1 finished and the stream said nothing about it.
    slice = fold(beat('2026-01-01T00:00:06Z', ['k2']), slice);
    expect(Object.keys(slice.bees).sort()).toEqual(['k2', 'w1']);
    // Another Warden's Heartbeat never ends this Warden's bees.
    slice = fold(
      [
        {
          type: 'telemetry',
          frame: telemetryFrame({ warden_id: 'w2', at: '2026-01-01T00:00:09Z' }),
        },
      ],
      slice,
    );
    expect(summaryCounters(slice)?.active_bees).toBe(3);
  });

  it('never counts a listed bee out between two frames of the same Heartbeat', () => {
    const at = (second: number): string => `2026-01-01T00:00:0${second}Z`;
    const warden = (second: number): SummaryEvent => ({
      type: 'telemetry',
      frame: telemetryFrame({ warden_id: 'w1', at: at(second), sub_bees: 2 }),
    });
    const row = (second: number, id: string): SummaryEvent => ({
      type: 'telemetry',
      frame: telemetryFrame({
        warden_id: 'w1',
        worker_id: id,
        worker_state: 'RUNNING',
        at: at(second),
      }),
    });
    let slice = fold([warden(1), row(1, 'k1'), row(1, 'k2')]);
    expect(summaryCounters(slice)?.active_bees).toBe(3);
    // The next Heartbeat lists both again: the count holds at every frame of it.
    const counts: (number | undefined)[] = [];
    for (const event of [warden(2), row(2, 'k1'), row(2, 'k2')]) {
      slice = fold([event], slice);
      counts.push(summaryCounters(slice)?.active_bees);
    }
    expect(counts).toEqual([3, 3, 3]);
    expect(slice.beats).toEqual({});
  });

  it('sweeps only when the last listed row has arrived, and heals if a Heartbeat is cut short', () => {
    const at = (second: number): string => `2026-01-01T00:00:0${second}Z`;
    const warden = (second: number, sub_bees: number): SummaryEvent => ({
      type: 'telemetry',
      frame: telemetryFrame({ warden_id: 'w1', at: at(second), sub_bees }),
    });
    const row = (second: number, id: string): SummaryEvent => ({
      type: 'telemetry',
      frame: telemetryFrame({
        warden_id: 'w1',
        worker_id: id,
        worker_state: 'RUNNING',
        at: at(second),
      }),
    });
    let slice = fold([warden(1, 2), row(1, 'k1'), row(1, 'k2')]);
    // Heartbeat 2 lists k2 only, so k1 stays counted until k2's row, the last, arrives.
    slice = fold([warden(2, 1)], slice);
    expect(Object.keys(slice.bees).sort()).toEqual(['k1', 'k2', 'w1']);
    slice = fold([row(2, 'k2')], slice);
    expect(Object.keys(slice.bees).sort()).toEqual(['k2', 'w1']);
    // Heartbeat 3 is cut short (its row never comes); Heartbeat 4 starts over and settles.
    slice = fold([warden(3, 1), warden(4, 0)], slice);
    expect(Object.keys(slice.bees)).toEqual(['w1']);
    expect(slice.beats).toEqual({});
  });

  it('forgets a swept bee in the thinking counters too', () => {
    const slice = fold([
      { type: 'telemetry', frame: telemetryFrame({ at: '2026-01-01T00:00:01Z' }) },
      {
        type: 'telemetry',
        frame: telemetryFrame({
          worker_id: 'k1',
          worker_state: 'RUNNING',
          at: '2026-01-01T00:00:01Z',
        }),
      },
      { type: 'episode', frame: episodeFrame({ principal: 'k1', is_autopilot: false }) },
      { type: 'episode', frame: episodeFrame({ principal: 'warden_1', is_autopilot: true }) },
      { type: 'telemetry', frame: telemetryFrame({ at: '2026-01-01T00:00:06Z' }) },
    ]);
    // The Warden itself stays; only the bee its Heartbeat dropped is forgotten.
    expect(Object.keys(slice.autopilot)).toEqual(['warden_1']);
  });

  it('counts only the states the server counts: a paused Worker or starting Warden is idle', () => {
    const slice = fold([
      { type: 'telemetry', frame: telemetryFrame({ warden_id: 'w1', warden_state: 'STARTING' }) },
      { type: 'telemetry', frame: telemetryFrame({ warden_id: 'w2', warden_state: 'WATCH' }) },
      {
        type: 'telemetry',
        frame: telemetryFrame({ warden_id: 'w2', worker_id: 'k1', worker_state: 'PAUSED' }),
      },
      {
        type: 'telemetry',
        frame: telemetryFrame({ warden_id: 'w2', worker_id: 'k2', worker_state: 'HANDING_OFF' }),
      },
    ]);
    expect(summaryCounters(slice)?.active_bees).toBe(2);
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

describe('the exit run: the strip lands on /v1/summary', () => {
  // What the phase 12 exit run saw: a Drone is SPAWNED, then RUNNING, and the Warden's next
  // Heartbeat lists no one. The server counts the Warden alone; so must the strip.
  it('folds SPAWNED, RUNNING, then a Heartbeat without the bee', () => {
    const at = (second: number): string => `2026-01-01T00:00:0${second}Z`;
    const warden = (second: number, sub_bees: number): SummaryEvent => ({
      type: 'telemetry',
      frame: telemetryFrame({
        warden_id: 'w1',
        warden_state: 'ACTIVE',
        at: at(second),
        sub_bees,
      }),
    });
    const drone = (second: number, state: 'SPAWNED' | 'RUNNING'): SummaryEvent => ({
      type: 'telemetry',
      frame: telemetryFrame({
        warden_id: 'w1',
        worker_id: 'drone_1',
        worker_state: state,
        at: at(second),
      }),
    });
    const cells: SummaryEvent[] = [
      {
        type: 'cell',
        frame: cellFrame({ id: 'real', kind: 'REAL', lease_state: 'OPEN', mode: 'WATCH' }),
      },
      { type: 'cell', frame: cellFrame({ id: 'virtual', kind: 'VIRTUAL', mode: 'ACTIVE' }) },
    ];
    let slice = fold([
      ...cells,
      warden(1, 1),
      drone(1, 'SPAWNED'),
      warden(2, 1),
      drone(2, 'RUNNING'),
    ]);
    expect(summaryCounters(slice)).toMatchObject({ active_bees: 2, cells_active: 1 });
    slice = fold([warden(3, 0)], slice);
    expect(summaryCounters(slice)).toMatchObject({
      active_bees: 1,
      cells_active: 1,
      cells_inactive: 1,
      cells_real: 1,
      cells_virtual: 1,
    });
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
    expect(summaryCounters(slice)).toEqual(SEED_STRIP);
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
