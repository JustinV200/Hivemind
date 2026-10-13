/**
 * Tests for the Forage reducer: the seed, frames, the request list and the bounds.
 *
 * The slice is pure, so each rule is checked on the reducer alone: the three reads become one
 * seeded state, a frame upserts its grant and replaces the headroom, the reserve moves only on a
 * re-read, a revoked grant stays gone even for a late seed, a request is paired with the Queen's
 * answer, and the lists never outgrow their bounds.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/forage/slice/.
 *
 * Key invariants:
 *   - Events are built with the support builders; only the fields a test is about are set.
 *
 * See Also:
 *   - tests/views/forage/forage_view.test.tsx for the view over this slice.
 */
import { describe, expect, it } from 'vitest';
import {
  initialForage,
  MAX_GONE,
  MAX_REQUESTS,
  reduceForage,
} from '../../../src/views/forage/slice';
import type { ForageEvent, ForageState } from '../../../src/views/forage/slice';
import {
  eventView,
  forageFrame,
  forageView,
  grant,
  headroom,
  llm,
  reserve,
  warden,
} from './support';

const run = (events: ForageEvent[], from: ForageState = initialForage): ForageState =>
  events.reduce(reduceForage, from);

const seed = (over = {}): ForageEvent => ({
  type: 'seeded',
  forage: forageView(over),
  wardens: { wardens: [warden()] },
  llm: llm(),
});

const frame = (
  kind: string,
  subject: string,
  over = {},
  payload = {},
  id?: string,
): ForageEvent => ({
  type: 'frame',
  frame: forageFrame(eventView(kind, subject, payload, id), over),
});

describe('seed', () => {
  it('folds the three reads into one seeded state', () => {
    const state = run([seed()]);
    expect(state.seeded).toBe(true);
    expect(state.grants.map((g) => g.id)).toEqual(['grant_1']);
    expect(state.capacities).toHaveLength(1);
    expect(state.headroom).toEqual(headroom());
    expect(state.reserve).toEqual(reserve());
    expect(state.wardens.map((w) => w.id)).toEqual(['warden_1']);
    expect(state.llm?.slots).toHaveLength(2);
  });

  it('opening the view starts from nothing', () => {
    const state = run([seed(), { type: 'open' }]);
    expect(state).toBe(initialForage);
  });

  it('keeps grants in the ledger order, holder then id', () => {
    const state = run([
      seed({
        grants: [
          grant({ id: 'g_b', holder: 'w_2' }),
          grant({ id: 'g_z', holder: 'w_1' }),
          grant({ id: 'g_a', holder: 'w_2' }),
        ],
      }),
    ]);
    expect(state.grants.map((g) => g.id)).toEqual(['g_z', 'g_a', 'g_b']);
  });

  it('records a failure for the view to show', () => {
    const state = run([{ type: 'failed', error: new Error('no') }]);
    expect(state.error).toBeInstanceOf(Error);
    expect(run([seed()], state).error).toBeNull();
  });
});

describe('frames', () => {
  it('updates a grant, the headroom, and leaves the reserve as read', () => {
    const before = run([seed()]);
    const state = run(
      [
        frame('forage.granted', 'grant_1', {
          grant: grant({ revision: 2, seats: 5, spent: 3 }),
          headroom: headroom({ shared_seats: 1, sub_bees: 4 }),
        }),
      ],
      before,
    );
    expect(state.grants).toHaveLength(1);
    expect(state.grants[0]).toMatchObject({ revision: 2, seats: 5, spent: 3 });
    expect(state.headroom).toEqual({ shared_seats: 1, sub_bees: 4 });
    expect(state.reserve).toBe(before.reserve);
  });

  it('adds a new grant in order', () => {
    const state = run([
      seed(),
      frame('forage.granted', 'g_0', { grant: grant({ id: 'g_0', holder: 'warden_0' }) }),
    ]);
    expect(state.grants.map((g) => g.id)).toEqual(['g_0', 'grant_1']);
  });

  it('drops a revoked or expired grant', () => {
    const revoked = run([seed(), frame('forage.revoked', 'grant_1')]);
    expect(revoked.grants).toEqual([]);
    const expired = run([seed(), frame('forage.expired', 'grant_1')]);
    expect(expired.grants).toEqual([]);
  });

  it('a re-read after a capacity report replaces capacities, reserve and grants', () => {
    const ticked = run([seed(), frame('forage.capacity_reported', 'cell_1')]);
    expect(ticked.capacityTicks).toBe(1);
    const state = run(
      [
        {
          type: 'reseeded',
          forage: forageView({ reserve: reserve({ seats: 9 }), capacities: [], grants: [] }),
        },
      ],
      ticked,
    );
    expect(state.reserve?.seats).toBe(9);
    expect(state.capacities).toEqual([]);
    expect(state.grants).toEqual([]);
    expect(state.capacityTicks).toBe(1);
  });

  it('leaves the capacity tick alone for other events', () => {
    expect(run([seed(), frame('forage.granted', 'grant_1')]).capacityTicks).toBe(0);
  });
});

describe('a frame that beats the seed', () => {
  it('wins over the seed on its grant and on the headroom', () => {
    const early = frame('forage.granted', 'grant_1', {
      grant: grant({ revision: 3 }),
      headroom: headroom({ shared_seats: 0 }),
    });
    const state = run([early, seed()]);
    expect(state.grants).toHaveLength(1);
    expect(state.grants[0]?.revision).toBe(3);
    expect(state.headroom?.shared_seats).toBe(0);
  });

  it('does not bring back a grant a frame already removed', () => {
    const state = run([frame('forage.revoked', 'grant_1'), seed()]);
    expect(state.grants).toEqual([]);
    expect(state.gone).toEqual(['grant_1']);
  });
});

describe('requests', () => {
  const asked = (kind = 'SPEND', id = 'ev_ask') =>
    frame('forage.requested', 'grant_1', {}, { request_kind: kind }, id);

  it('is pending from the ask, and answered by a grant', () => {
    const pending = run([asked()]);
    expect(pending.requests).toMatchObject([{ grantId: 'grant_1', decision: 'pending' }]);
    expect(pending.requests[0]?.requestKind).toBe('SPEND');
    const state = run([frame('forage.granted', 'grant_1', {}, {}, 'ev_ans')], pending);
    expect(state.requests[0]).toMatchObject({ decision: 'granted', reason: null });
    expect(state.requests[0]?.decidedAt).not.toBeNull();
  });

  it('carries the denial reason code', () => {
    const state = run([asked(), frame('forage.denied', 'grant_1', {}, { reason: 'no_headroom' })]);
    expect(state.requests[0]).toMatchObject({ decision: 'denied', reason: 'no_headroom' });
  });

  it('ignores an answer with no request open under it', () => {
    const before = run([seed()]);
    const state = run([frame('forage.denied', 'task_9', {}, { reason: 'x' })], before);
    expect(state.requests).toEqual([]);
  });

  it('answers only the matching kind and only once', () => {
    const state = run([
      asked('SPEND', 'a'),
      asked('SHARED_SEATS', 'b'),
      frame('forage.denied', 'grant_1', {}, { request_kind: 'SPEND', reason: 'r' }),
    ]);
    const byId = Object.fromEntries(state.requests.map((r) => [r.id, r.decision]));
    expect(byId).toEqual({ a: 'denied', b: 'pending' });
  });

  it('does not repeat a replayed ask', () => {
    expect(run([asked(), asked()]).requests).toHaveLength(1);
  });

  it('is bounded to the newest MAX_REQUESTS', () => {
    const many = Array.from({ length: MAX_REQUESTS + 5 }, (_, i) => asked('SPEND', `ev_${i}`));
    const state = run(many);
    expect(state.requests).toHaveLength(MAX_REQUESTS);
    expect(state.requests[0]?.id).toBe(`ev_${MAX_REQUESTS + 4}`);
  });

  it('remembers at most MAX_GONE revoked grants', () => {
    const many = Array.from({ length: MAX_GONE + 3 }, (_, i) => frame('forage.revoked', `g_${i}`));
    const state = run(many);
    expect(state.gone).toHaveLength(MAX_GONE);
    expect(state.gone.at(-1)).toBe(`g_${MAX_GONE + 2}`);
  });
});
