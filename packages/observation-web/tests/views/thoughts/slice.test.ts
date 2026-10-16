/**
 * Tests for the Thoughts reducer: seed, follow queueing, the bound, paging and the bee filter.
 *
 * The slice is pure, so each rule is checked on the reducer alone: a seed lands newest first, a
 * frame for another bee is ignored, Follow off queues instead of moving the list, the list never
 * holds more than 500 episodes (and opening the same bee again after that cut starts clean, so
 * paging is offered again), and an older page appends without repeating an episode id.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/thoughts/slice.ts.
 *
 * Key invariants:
 *   - Episodes are built with ids and times only; nothing else about them matters here.
 *
 * See Also:
 *   - tests/views/thoughts/thoughts_view.test.tsx for the view over this slice.
 */
import { describe, expect, it } from 'vitest';
import { initialThoughts, MAX_EPISODES, reduceThoughts } from '../../../src/views/thoughts/slice';
import type { ThoughtsEvent, ThoughtsState } from '../../../src/views/thoughts/slice';
import {
  decisionView,
  episodeFrame,
  episodeView,
  thoughtsPage,
  trailFrame,
} from '../../support/frames';

const run = (events: ThoughtsEvent[], from: ThoughtsState = initialThoughts): ThoughtsState =>
  events.reduce(reduceThoughts, from);

// Newer ids have larger numbers; `at` follows, one second apart.
const ep = (n: number, principal = 'queen') =>
  episodeView({
    id: `ep_${n}`,
    principal,
    at: new Date(Date.UTC(2026, 0, 1, 0, 0, n)).toISOString(),
  });
const frame = (n: number, principal = 'queen'): ThoughtsEvent => ({
  type: 'frame',
  frame: episodeFrame(ep(n, principal)),
});
const ids = (state: ThoughtsState) => state.episodes.map((e) => e.id);
const opened = run([{ type: 'open', principal: 'queen' }]);

describe('seed', () => {
  it('lands newest first with the decisions and the cursor', () => {
    const page = thoughtsPage({
      episodes: [ep(3), ep(2)],
      decisions: [decisionView({ id: 'dec_1' })],
      next_cursor: 'c1',
    });
    const state = run([{ type: 'seeded', principal: 'queen', page }], opened);
    expect(ids(state)).toEqual(['ep_3', 'ep_2']);
    expect(state.decisions).toHaveLength(1);
    expect(state).toMatchObject({ seeded: true, nextCursor: 'c1', error: null });
  });

  it('keeps a frame that arrived before the answer, once, in time order', () => {
    const page = thoughtsPage({ episodes: [ep(3), ep(2)] });
    const state = run([frame(4), frame(3), { type: 'seeded', principal: 'queen', page }], opened);
    expect(ids(state)).toEqual(['ep_4', 'ep_3', 'ep_2']);
  });

  it('ignores an answer for a bee the reader already left', () => {
    const page = thoughtsPage({ episodes: [ep(1)] });
    const state = run([{ type: 'seeded', principal: 'w_9', page }], opened);
    expect(state).toBe(opened);
  });

  it('starts clean for another bee and keeps what is held for the same bee', () => {
    const held = run([frame(2)], opened);
    expect(run([{ type: 'open', principal: 'queen' }], held)).toBe(held);
    expect(run([{ type: 'open', principal: 'w_9' }], held)).toMatchObject({
      principal: 'w_9',
      episodes: [],
    });
  });

  it('records a failure and clears it when the bee is opened again', () => {
    const failed = run([{ type: 'failed', principal: 'queen', error: new Error('no') }], opened);
    expect(failed.error).toBeInstanceOf(Error);
    expect(run([{ type: 'open', principal: 'queen' }], failed).error).toBeNull();
  });
});

describe('follow', () => {
  it('prepends a new episode when on', () => {
    expect(ids(run([frame(1), frame(2)], opened))).toEqual(['ep_2', 'ep_1']);
  });

  it('queues behind the list when off, then reveals on request, newest first', () => {
    const state = run([frame(1), { type: 'follow', on: false }, frame(2), frame(3)], opened);
    expect(ids(state)).toEqual(['ep_1']);
    expect(state.queued.map((e) => e.id)).toEqual(['ep_3', 'ep_2']);
    const revealed = run([{ type: 'reveal' }], state);
    expect(ids(revealed)).toEqual(['ep_3', 'ep_2', 'ep_1']);
    expect(revealed.queued).toEqual([]);
  });

  it('reveals what waited when Follow is turned back on', () => {
    const state = run(
      [{ type: 'follow', on: false }, frame(1), { type: 'follow', on: true }],
      opened,
    );
    expect(ids(state)).toEqual(['ep_1']);
    expect(state.follow).toBe(true);
  });

  it('ignores a repeat of an episode it already holds, listed or queued', () => {
    const listed = run([frame(1), frame(1)], opened);
    expect(ids(listed)).toEqual(['ep_1']);
    const queued = run([{ type: 'follow', on: false }, frame(2), frame(2)], opened);
    expect(queued.queued).toHaveLength(1);
  });
});

describe('principal filter', () => {
  it('keeps only the open bee, and nothing before a bee is open', () => {
    expect(run([frame(1, 'w_9')], opened)).toBe(opened);
    expect(run([frame(1)], initialThoughts)).toBe(initialThoughts);
  });
});

describe('bound', () => {
  it('holds at most 500 episodes, dropping the oldest, and withdraws the cursor', () => {
    const page = thoughtsPage({ episodes: [ep(1)], next_cursor: 'c1' });
    let state = run([{ type: 'seeded', principal: 'queen', page }], opened);
    const frames = Array.from({ length: MAX_EPISODES }, (_, i) => frame(i + 2));
    state = run(frames, state);
    expect(state.episodes).toHaveLength(MAX_EPISODES);
    expect(state.episodes[0]!.id).toBe(`ep_${MAX_EPISODES + 1}`);
    expect(ids(state)).not.toContain('ep_1');
    expect(state).toMatchObject({ truncated: true, nextCursor: null });
  });

  it('starts clean when the same bee is opened again after the cut, so Older comes back', () => {
    const page = thoughtsPage({ episodes: [ep(1)], next_cursor: 'c1' });
    let state = run([{ type: 'seeded', principal: 'queen', page }], opened);
    state = run(
      Array.from({ length: MAX_EPISODES }, (_, i) => frame(i + 2)),
      state,
    );
    expect(state).toMatchObject({ truncated: true, nextCursor: null });
    // Leaving and coming back: the held list goes, the fresh seed's cursor is kept.
    const reopened = run([{ type: 'open', principal: 'queen' }], state);
    expect(reopened).toMatchObject({ principal: 'queen', seeded: false, truncated: false });
    expect(reopened.episodes).toHaveLength(0);
    const fresh = thoughtsPage({ episodes: [ep(600)], next_cursor: 'c2' });
    const seeded = run([{ type: 'seeded', principal: 'queen', page: fresh }], reopened);
    expect(seeded).toMatchObject({ nextCursor: 'c2', truncated: false });
  });

  it('bounds the queue too', () => {
    const state = run(
      [
        { type: 'follow', on: false },
        ...Array.from({ length: MAX_EPISODES + 5 }, (_, i) => frame(i + 1)),
      ],
      opened,
    );
    expect(state.queued).toHaveLength(MAX_EPISODES);
  });
});

describe('older page', () => {
  const seeded = run(
    [
      {
        type: 'seeded',
        principal: 'queen',
        page: thoughtsPage({ episodes: [ep(5), ep(4)], next_cursor: 'c1' }),
      },
    ],
    opened,
  );

  it('appends below, without repeating an id, and moves the cursor on', () => {
    const page = thoughtsPage({ episodes: [ep(4), ep(3), ep(2)], next_cursor: null });
    const state = run([{ type: 'older', principal: 'queen', cursor: 'c1', page }], seeded);
    expect(ids(state)).toEqual(['ep_5', 'ep_4', 'ep_3', 'ep_2']);
    expect(state.nextCursor).toBeNull();
  });

  it('ignores a page fetched with a cursor that is no longer next', () => {
    const page = thoughtsPage({ episodes: [ep(1)] });
    expect(run([{ type: 'older', principal: 'queen', cursor: 'old', page }], seeded)).toBe(seeded);
  });
});

describe("the Queen's real principal", () => {
  const hiveId = 'hive_01QUEEN';
  const seeded = run(
    [{ type: 'seeded', principal: 'queen', page: thoughtsPage({ principal: hiveId }) }],
    opened,
  );

  it('takes the resolved principal from the seed', () => {
    expect(seeded.resolved).toBe(hiveId);
    expect(opened.resolved).toBeNull();
  });

  it('accepts a frame under either the requested name or the resolved principal', () => {
    const state = run([frame(1, hiveId), frame(2, 'queen'), frame(3, 'warden_a')], seeded);
    expect(ids(state)).toEqual(['ep_2', 'ep_1']);
  });

  it('does not accept the Hive id before the seed has said it is hers', () => {
    expect(ids(run([frame(1, hiveId)], opened))).toEqual([]);
  });

  it('starts clean for another bee, forgetting the resolved principal', () => {
    expect(run([{ type: 'open', principal: 'w_9' }], seeded).resolved).toBeNull();
  });
});

describe("the Queen's decisions from the trail", () => {
  const decision = (id: string, n: number, kind = 'queen.decided', payload = {}) =>
    ({
      type: 'decision',
      frame: trailFrame({
        id,
        kind,
        payload,
        at: new Date(Date.UTC(2026, 0, 1, 0, 0, n)).toISOString(),
      }),
    }) satisfies ThoughtsEvent;

  it('reshapes a queen.decided and an alarm.handled event like the seed does', () => {
    const state = run(
      [
        decision('d1', 1, 'queen.decided', { action: 'REBIND', cause: 'lease_gone' }),
        decision('d2', 2, 'alarm.handled', { action: 'RECORD' }),
      ],
      opened,
    );
    expect(state.decisions).toEqual([
      expect.objectContaining({ id: 'd2', alarm_id: 'cell_1', action: 'RECORD', reason: null }),
      expect.objectContaining({ id: 'd1', alarm_id: null, action: 'REBIND', reason: 'lease_gone' }),
    ]);
  });

  it('ignores other kinds, a repeat, and any bee but the Queen', () => {
    const once = run([decision('d1', 1)], opened);
    expect(run([decision('d1', 1)], once)).toBe(once);
    expect(run([decision('t1', 2, 'task.started')], opened)).toBe(opened);
    const worker = run([{ type: 'open', principal: 'w_9' }]);
    expect(run([decision('d3', 3)], worker)).toBe(worker);
  });

  it('queues behind Follow off and reveals with the episodes', () => {
    const off = run([{ type: 'follow', on: false }, decision('d1', 1)], opened);
    expect(off.decisions).toEqual([]);
    expect(off.queuedDecisions).toHaveLength(1);
    expect(run([{ type: 'reveal' }], off)).toMatchObject({ queuedDecisions: [] });
    expect(run([{ type: 'reveal' }], off).decisions).toHaveLength(1);
  });

  it('stays bounded', () => {
    const events = Array.from({ length: MAX_EPISODES + 20 }, (_, i) => decision(`d${i}`, i));
    expect(run(events, opened).decisions).toHaveLength(MAX_EPISODES);
  });
});
