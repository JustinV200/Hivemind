/**
 * Tests for the Cell page reducer: seed, Cell frames, telemetry folding and the trail counter.
 *
 * The slice is pure, so each rule is checked on the reducer alone: a seed lands the page, a
 * `CellFrame` for this Cell updates the header fields and one for another Cell changes nothing, a
 * telemetry sample from this Cell's Warden updates one bee's gauge (and one from another Warden is
 * ignored), and only a trail event naming this Cell or its Warden moves the counter the hook
 * watches. Nothing before the seed is folded.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/cell/slice.ts.
 *
 * Key invariants:
 *   - "Ignored" means the very same state object comes back, which the store reads as no change.
 *
 * See Also:
 *   - tests/views/cell/cell_view.test.tsx for the view and the re-read over this slice.
 */
import { describe, expect, it } from 'vitest';
import { initialCellPage, MAX_BEES, reduceCellPage } from '../../../src/views/cell/slice';
import type { CellPageEvent, CellPageState } from '../../../src/views/cell/slice';
import { beeRow, cellFrameOf, pageView, sampleFrame, trailFrame } from './support';

const run = (events: CellPageEvent[], from: CellPageState = initialCellPage): CellPageState =>
  events.reduce(reduceCellPage, from);

const open: CellPageEvent = { type: 'open', cellId: 'cell_1' };
const seed: CellPageEvent = { type: 'seeded', cellId: 'cell_1', page: pageView() };
const seeded = run([open, seed]);
const worker = (state: CellPageState) => state.page!.bees.find((b) => b.id === 'worker_1')!;

describe('seed', () => {
  it('lands the page for the open Cell and clears an earlier failure', () => {
    const failed = run([open, { type: 'failed', cellId: 'cell_1', error: new Error('no') }]);
    expect(failed.error).toBeInstanceOf(Error);
    const state = run([seed], failed);
    expect(state.page?.cell.id).toBe('cell_1');
    expect(state.error).toBeNull();
  });

  it('ignores an answer, or a failure, for a Cell the reader already left', () => {
    const opened = run([{ type: 'open', cellId: 'cell_2' }]);
    expect(run([seed], opened)).toBe(opened);
    expect(run([{ type: 'failed', cellId: 'cell_1', error: 1 }], opened)).toBe(opened);
  });

  it('starts every visit clean', () => {
    const again = run([open], seeded);
    expect(again).toMatchObject({ cellId: 'cell_1', page: null, trailTicks: 0 });
  });
});

describe('Cell frames', () => {
  it('replace the Cell, so the header fields change at once', () => {
    const state = run(
      [
        {
          type: 'cell',
          frame: cellFrameOf({
            comb_shield: 'NIGHT_VEIL',
            mask_state: 'QUEEN_FORCED',
            mode: 'WATCH',
          }),
        },
      ],
      seeded,
    );
    expect(state.page!.cell).toMatchObject({
      comb_shield: 'NIGHT_VEIL',
      mask_state: 'QUEEN_FORCED',
      mode: 'WATCH',
    });
    // The rest of the page is the seed's.
    expect(state.page!.bees).toBe(seeded.page!.bees);
  });

  it('for another Cell change nothing', () => {
    const state = run([{ type: 'cell', frame: cellFrameOf({ id: 'cell_2' }) }], seeded);
    expect(state).toBe(seeded);
  });

  it('are not folded before the seed', () => {
    const opened = run([open]);
    expect(run([{ type: 'cell', frame: cellFrameOf() }], opened)).toBe(opened);
  });
});

describe('telemetry', () => {
  it('updates one sub-bee: its gauge, status, task and spend', () => {
    const state = run([{ type: 'telemetry', frame: sampleFrame() }], seeded);
    expect(worker(state)).toMatchObject({
      context_tokens_used: 64000,
      context_window: 128000,
      context_fraction: 0.5,
      spend_usd: 0.75,
      status: 'RUNNING',
      task_id: 'task_1',
      role: 'DRONE',
    });
    // The Warden's row is the same object.
    expect(state.page!.bees[0]).toBe(seeded.page!.bees[0]);
  });

  it("updates the Warden's own row from its own report", () => {
    const frame = sampleFrame({ worker_id: null, worker_state: null, warden_state: 'WATCH' });
    const state = run([{ type: 'telemetry', frame }], seeded);
    expect(state.page!.bees[0]).toMatchObject({ id: 'warden_1', status: 'WATCH' });
  });

  it('adds a row for a sub-bee the page did not list, with an unknown role', () => {
    const state = run(
      [{ type: 'telemetry', frame: sampleFrame({ worker_id: 'worker_9' }) }],
      seeded,
    );
    expect(state.page!.bees.at(-1)).toMatchObject({ id: 'worker_9', role: 'UNKNOWN' });
  });

  it("ignores another Warden's samples and anything before the seed", () => {
    const other = sampleFrame({ warden_id: 'warden_2' });
    expect(run([{ type: 'telemetry', frame: other }], seeded)).toBe(seeded);
    const opened = run([open]);
    expect(run([{ type: 'telemetry', frame: sampleFrame() }], opened)).toBe(opened);
  });

  it('reads a window of zero as an unreported gauge', () => {
    const state = run([{ type: 'telemetry', frame: sampleFrame({ context_window: 0 }) }], seeded);
    expect(worker(state).context_fraction).toBeNull();
  });

  it('stops adding rows at the bound but still updates known bees', () => {
    const bees = Array.from({ length: MAX_BEES }, (_, i) => beeRow({ id: `b_${i}` }));
    const full = run([open, { type: 'seeded', cellId: 'cell_1', page: pageView({ bees }) }]);
    const added = run([{ type: 'telemetry', frame: sampleFrame({ worker_id: 'extra' }) }], full);
    expect(added).toBe(full);
    const known = run([{ type: 'telemetry', frame: sampleFrame({ worker_id: 'b_0' }) }], full);
    expect(known.page!.bees[0]!.context_tokens_used).toBe(64000);
  });
});

describe('trail', () => {
  const ticks = (event: Parameters<typeof trailFrame>[0]) =>
    run([{ type: 'trail', frame: trailFrame(event) }], seeded).trailTicks;

  it('counts an event that names this Cell or its Warden', () => {
    expect(ticks({ subject_id: 'cell_1' })).toBe(1);
    expect(ticks({ subject_id: 'x', actor: 'warden_1' })).toBe(1);
    expect(ticks({ subject_id: 'x', payload: { cell_id: 'cell_1' } })).toBe(1);
    expect(ticks({ subject_id: 'x', payload: { warden_id: 'warden_1' } })).toBe(1);
  });

  it('counts nothing for an event about another Cell', () => {
    const state = run(
      [
        {
          type: 'trail',
          frame: trailFrame({ subject_id: 'cell_2', payload: { cell_id: 'cell_2' } }),
        },
      ],
      seeded,
    );
    expect(state).toBe(seeded);
  });

  it('counts nothing before the seed', () => {
    const opened = run([open]);
    expect(run([{ type: 'trail', frame: trailFrame() }], opened)).toBe(opened);
  });
});
