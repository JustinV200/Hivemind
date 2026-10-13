/**
 * Tests for the live feeds: seed, streams, session end and shutdown.
 *
 * `startFeeds` is the only thing that connects the store to the Entrance, so it is tested end to
 * end against the fake browser: it must read the summary once, open exactly the three streams the
 * strip needs, fold their frames into the store, turn a 4410 into a session end, record a
 * refusal without ending anything, and leave no socket or timer behind when stopped.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/store/feed.ts with the real client.
 *
 * Key invariants:
 *   - No request other than the two seeds is made: views never poll.
 *
 * See Also:
 *   - docs/adr/0049-observation-hive-dashboard-stack.md (streams).
 */
import { describe, expect, it } from 'vitest';
import { createLandingBoard } from '../../src/landing_board/client';
import { APP_SLICES } from '../../src/app';
import { createHiveStore, startFeeds, summaryCounters } from '../../src/store';
import { createFakeBrowser, flush } from '../support/fake_browser';
import { cellFrame, SEED, SEED_STRIP, telemetryFrame } from '../support/frames';
import { storeSession } from '../support/session_fixture';

async function started(options: { seed?: boolean } = {}) {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  const store = createHiveStore(APP_SLICES);
  const board = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await board.session.restore();
  fake.respond('GET /v1/devices/me', 200, { name: 'Pixel' });
  if (options.seed !== false) fake.respond('GET /v1/summary', 200, SEED);
  const stop = startFeeds({ client: board, store, nowMs: () => fake.now() });
  await flush();
  return { fake, store, stop };
}

describe('startFeeds', () => {
  it('seeds the summary and the device name once, and opens the three streams', async () => {
    const { fake, store } = await started();
    expect(fake.requests.map((r) => `${r.method} ${r.target}`).sort()).toEqual([
      'GET /v1/devices/me',
      'GET /v1/summary',
    ]);
    expect(fake.sockets.map((s) => s.url)).toEqual([
      'wss://hive.test/v1/cells/stream',
      'wss://hive.test/v1/telemetry/stream',
      'wss://hive.test/v1/episodes/stream',
    ]);
    expect(summaryCounters(store.getSnapshot().summary)).toEqual(SEED_STRIP);
    expect(store.getSnapshot().connection.streams.cells.state).toBe('connecting');
  });

  it('folds frames into the store and marks the streams live', async () => {
    const { fake, store } = await started({ seed: false });
    fake.sockets[0]!.open();
    fake.sockets[0]!.receive(cellFrame({ id: 'a', virtual_status: 'READY' }));
    fake.sockets[1]!.open();
    fake.sockets[1]!.receive(telemetryFrame({ worker_id: 'k1', worker_state: 'RUNNING' }));
    const counters = summaryCounters(store.getSnapshot().summary);
    expect(counters).toMatchObject({ cells_active: 1, active_bees: 1 });
    expect(store.getSnapshot().connection.streams.cells.state).toBe('live');
  });

  it('works without a seed when /v1/summary is not served yet', async () => {
    const { store } = await started({ seed: false });
    expect(summaryCounters(store.getSnapshot().summary)).toBeNull();
    expect(store.getSnapshot().session.status).toBe('restoring');
  });

  it('ends the session on 4410 and only records a refusal on 4403', async () => {
    const { fake, store } = await started();
    fake.sockets[0]!.open();
    fake.sockets[1]!.serverClose(4403);
    expect(store.getSnapshot().connection.streams.telemetry.state).toBe('refused');
    expect(store.getSnapshot().session.status).not.toBe('ended');
    fake.sockets[0]!.serverClose(4410);
    // The slice only ends an active session, so make one active first.
    store.dispatch({
      slice: 'session',
      event: {
        type: 'opened',
        session: { deviceId: 'd', listener: 'remote', expiresAt: 'x', needsStepUp: false },
      },
    });
    fake.sockets[2]!.serverClose(4410);
    expect(store.getSnapshot().session).toMatchObject({
      status: 'ended',
      notice: 'Your session ended. Log in again.',
    });
  });

  it('closes every socket and leaves no timer when stopped', async () => {
    const { fake, stop } = await started();
    fake.sockets.forEach((s) => s.open());
    await flush();
    stop();
    expect(fake.sockets.every((s) => s.closedWith === 1000)).toBe(true);
    expect(fake.pendingTimers()).toBe(0);
  });
});
