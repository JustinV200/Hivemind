/**
 * Tests for the Attendant reducer: seed, task frames, the re-read counter and what is ignored.
 *
 * The slice is pure, so each rule is checked on the reducer alone: a seed lands the view, a frame
 * for this principal's task updates that node's status and placement, a frame for another
 * principal (or one before the seed) changes nothing, and a frame for a task the graph does not
 * hold only moves the counter the hook watches for a re-read.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/attendant/slice.ts.
 *
 * Key invariants:
 *   - "Ignored" means the very same state object comes back, which the store reads as no change.
 *
 * See Also:
 *   - tests/views/attendant/attendant_view.test.tsx for the view and the re-read over this slice.
 */
import { describe, expect, it } from 'vitest';
import { initialAttendant, reduceAttendant } from '../../../src/views/attendant/slice';
import type { AttendantEvent, AttendantState } from '../../../src/views/attendant/slice';
import { attendantView, diamond, graphFrame } from './support';

const run = (events: AttendantEvent[], from: AttendantState = initialAttendant): AttendantState =>
  events.reduce(reduceAttendant, from);

const open: AttendantEvent = { type: 'open', principal: 'queen' };
const seed: AttendantEvent = { type: 'seeded', principal: 'queen', view: diamond() };
const seeded = run([open, seed]);
const node = (state: AttendantState, id: string) =>
  state.view!.nodes.find((n) => n.task_id === id)!;

describe('seed', () => {
  it('lands the view for the open principal and clears an earlier failure', () => {
    const failed = run([open, { type: 'failed', principal: 'queen', error: new Error('no') }]);
    expect(failed.error).toBeInstanceOf(Error);
    const state = run([seed], failed);
    expect(state.view?.nodes).toHaveLength(4);
    expect(state.error).toBeNull();
  });

  it('ignores an answer, or a failure, for a principal the reader already left', () => {
    const opened = run([{ type: 'open', principal: 'warden_1' }]);
    expect(run([seed], opened)).toBe(opened);
    expect(run([{ type: 'failed', principal: 'queen', error: 1 }], opened)).toBe(opened);
  });

  it('starts every visit clean', () => {
    const again = run([open], seeded);
    expect(again).toMatchObject({ principal: 'queen', view: null, frameTicks: 0 });
  });

  it('replaces what frames folded since the last read', () => {
    const moved = run([{ type: 'task', frame: graphFrame('queen', { id: 'task_2' }) }], seeded);
    const fresh = attendantView({ nodes: [], inbox: [] });
    const state = run([{ type: 'seeded', principal: 'queen', view: fresh }], moved);
    expect(state.view).toBe(fresh);
  });
});

describe('task frames', () => {
  it("update the node's status and placement and count toward a re-read", () => {
    const state = run(
      [
        {
          type: 'task',
          frame: graphFrame('queen', {
            id: 'task_3',
            status: 'ASSIGNED',
            cell_id: 'cell_9',
            warden_id: 'warden_9',
          }),
        },
      ],
      seeded,
    );
    expect(node(state, 'task_3')).toMatchObject({
      status: 'ASSIGNED',
      cell_id: 'cell_9',
      warden_id: 'warden_9',
    });
    expect(state.frameTicks).toBe(1);
  });

  it('keep what a frame does not carry: the role and the Alarm count', () => {
    const state = run(
      [{ type: 'task', frame: graphFrame('queen', { id: 'task_2', status: 'FAILED' }) }],
      seeded,
    );
    expect(node(state, 'task_2')).toMatchObject({
      status: 'FAILED',
      open_alarms: 2,
      role: 'DRONE',
    });
  });

  it('leave the other nodes, the edges and the lists as they were', () => {
    const state = run(
      [{ type: 'task', frame: graphFrame('queen', { id: 'task_1', status: 'FAILED' }) }],
      seeded,
    );
    expect(node(state, 'task_2')).toBe(node(seeded, 'task_2'));
    expect(state.view!.edges).toBe(seeded.view!.edges);
  });

  it("ignore another principal's task, even one this graph holds", () => {
    const state = run(
      [{ type: 'task', frame: graphFrame('warden_1', { id: 'task_1', status: 'FAILED' }) }],
      seeded,
    );
    expect(state).toBe(seeded);
  });

  it('are ignored before the seed has landed', () => {
    const opened = run([open]);
    expect(run([{ type: 'task', frame: graphFrame('queen') }], opened)).toBe(opened);
    expect(run([{ type: 'task', frame: graphFrame('queen') }])).toBe(initialAttendant);
  });

  it('for a task the graph does not hold only count, and never add a node', () => {
    const state = run([{ type: 'task', frame: graphFrame('queen', { id: 'task_99' }) }], seeded);
    expect(state.view!.nodes).toBe(seeded.view!.nodes);
    expect(state.frameTicks).toBe(1);
  });
});
