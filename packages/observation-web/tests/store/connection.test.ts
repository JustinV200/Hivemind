/**
 * Tests for the connection and session reducers.
 *
 * The top bar's word and the strip's stale rule come from these slices, so the tests pin the
 * words, the time a stream went down, and what a refusal does (it is not "down", it will not
 * come back). The session reducer decides which screen shows, so its transitions are checked
 * too, including that a repeated end does not overwrite the first notice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/store/connection.ts and src/store/session.ts.
 *
 * Key invariants:
 *   - Times are passed in; nothing here reads a clock.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 4.4.
 */
import { describe, expect, it } from 'vitest';
import {
  connectionWord,
  initialConnection,
  reduceConnection,
  refusedStreams,
  staleSince,
} from '../../src/store/connection';
import type { ConnectionEvent } from '../../src/store/connection';
import { initialSession, reduceSession, SESSION_ENDED_NOTICE } from '../../src/store/session';

const fold = (events: ConnectionEvent[]) => events.reduce(reduceConnection, initialConnection);
const live = (at: number): ConnectionEvent[] =>
  (['cells', 'telemetry', 'episodes'] as const).map((name) => ({
    type: 'stream',
    name,
    state: 'live',
    at,
  }));

describe('connection', () => {
  it('is offline before anything connects, reconnecting while connecting, live when all are', () => {
    expect(connectionWord(initialConnection)).toBe('offline');
    expect(connectionWord(fold([{ type: 'reset', at: 1 }]))).toBe('reconnecting');
    expect(connectionWord(fold([{ type: 'reset', at: 1 }, ...live(2)]))).toBe('live');
  });

  it('is current while live and stale since the first stream went down', () => {
    const live1 = fold([{ type: 'reset', at: 1 }, ...live(2)]);
    expect(staleSince(live1)).toBeNull();
    const down = reduceConnection(live1, {
      type: 'stream',
      name: 'cells',
      state: 'reconnecting',
      at: 50,
    });
    const later = reduceConnection(down, {
      type: 'stream',
      name: 'episodes',
      state: 'reconnecting',
      at: 90,
    });
    expect(connectionWord(later)).toBe('reconnecting');
    expect(staleSince(later)).toBe(50);
  });

  it('keeps the original time when a state repeats', () => {
    const once = fold([{ type: 'stream', name: 'cells', state: 'reconnecting', at: 5 }]);
    expect(
      reduceConnection(once, { type: 'stream', name: 'cells', state: 'reconnecting', at: 9 }),
    ).toBe(once);
  });

  it('treats a refused stream as neither down nor live, and lists it', () => {
    const slice = fold([
      { type: 'reset', at: 1 },
      { type: 'stream', name: 'cells', state: 'live', at: 2 },
      { type: 'stream', name: 'telemetry', state: 'refused', at: 2 },
      { type: 'stream', name: 'episodes', state: 'refused', at: 2 },
    ]);
    expect(connectionWord(slice)).toBe('live');
    expect(staleSince(slice)).toBeNull();
    expect(refusedStreams(slice)).toEqual(['telemetry', 'episodes']);
  });
});

describe('session', () => {
  const opened = { deviceId: 'd', listener: 'loopback', expiresAt: 'x', needsStepUp: true };

  it('goes from restoring to active with the listener and step-up flag', () => {
    const slice = reduceSession(initialSession, { type: 'restored', session: opened });
    expect(slice).toMatchObject({ status: 'active', listener: 'loopback', needsStepUp: true });
    expect(reduceSession(initialSession, { type: 'restored', session: null }).status).toBe('none');
  });

  it('reads any listener but loopback as remote', () => {
    const slice = reduceSession(initialSession, {
      type: 'opened',
      session: { ...opened, listener: 'lan' },
    });
    expect(slice.listener).toBe('remote');
  });

  it('names the device, clears step-up, and ends with one notice', () => {
    let slice = reduceSession(initialSession, { type: 'opened', session: opened });
    slice = reduceSession(slice, { type: 'named', name: 'Pixel' });
    slice = reduceSession(slice, { type: 'stepped_up' });
    expect(slice).toMatchObject({ deviceName: 'Pixel', needsStepUp: false });
    const ended = reduceSession(slice, { type: 'ended', notice: SESSION_ENDED_NOTICE });
    expect(ended).toMatchObject({ status: 'ended', notice: SESSION_ENDED_NOTICE, listener: null });
    // Several sockets close at once; the second end changes nothing.
    expect(reduceSession(ended, { type: 'ended', notice: 'other' })).toBe(ended);
    expect(reduceSession(ended, { type: 'left' }).status).toBe('none');
  });
});
