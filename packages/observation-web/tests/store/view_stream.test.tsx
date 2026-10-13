/**
 * Tests for `useViewStream`: a view's own stream, opened on mount and closed on unmount.
 *
 * Views never call `openStream` themselves; this hook is how they keep live. It is tested with the
 * real client over the fake browser: it must open the named stream, fold each frame into the named
 * slice, resume from the cursor on 4409, report the stream's state in the connection slice (and
 * remove it again), and close the socket when the view goes away.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/store/react.tsx and src/store/view_stream.ts.
 *
 * Key invariants:
 *   - The probe folds episode frames into the summary slice, which the app's table always holds.
 *   - Sockets are driven by the test; no real network or timer runs.
 *
 * See Also:
 *   - tests/landing_board/client/socket.test.ts for the reconnect policy itself.
 */
import { act, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { APP_SLICES } from '../../src/app';
import { createLandingBoard } from '../../src/landing_board/client';
import type { components } from '../../src/landing_board/types';
import { createHiveStore, HiveProvider, useViewStream } from '../../src/store';
import { createFakeBrowser, flush } from '../support/fake_browser';
import { episodeFrame } from '../support/frames';
import { storeSession } from '../support/session_fixture';

type EpisodeFrame = components['schemas']['EpisodeFrame'];
const PATH = '/v1/episodes/stream';
const toEvent = (frame: EpisodeFrame) => ({ type: 'episode' as const, frame });
const resume = {
  cursorOf: (frame: EpisodeFrame) => frame.episode.id,
  query: (cursor: string) => `?after=${cursor}`,
};

function Probe(props: { withResume?: boolean }) {
  useViewStream(PATH, 'summary', toEvent, props.withResume ? resume : undefined);
  return null;
}

async function mounted(withResume = false) {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await client.session.restore();
  const store = createHiveStore(APP_SLICES);
  const view = render(
    <HiveProvider hive={{ store, client }}>
      <Probe withResume={withResume} />
    </HiveProvider>,
  );
  return { fake, store, view };
}

describe('useViewStream', () => {
  it('opens the stream on mount and closes it for good on unmount', async () => {
    const { fake, view } = await mounted();
    expect(fake.sockets.map((s) => s.url)).toEqual(['wss://hive.test' + PATH]);
    fake.sockets[0]!.open();
    await flush();
    view.unmount();
    expect(fake.sockets[0]!.closedWith).toBe(1000);
    expect(fake.pendingTimers()).toBe(0);
  });

  it('folds each frame into the named slice', async () => {
    const { fake, store } = await mounted();
    fake.sockets[0]!.open();
    act(() => fake.sockets[0]!.receive(episodeFrame({ id: 'ep_1', principal: 'w_1' })));
    expect(Object.keys(store.getSnapshot().summary.autopilot)).toEqual(['w_1']);
  });

  it('resumes from the last cursor when the Entrance says it fell behind', async () => {
    const { fake } = await mounted(true);
    fake.sockets[0]!.open();
    act(() => fake.sockets[0]!.receive(episodeFrame({ id: 'ep_7' })));
    act(() => fake.sockets[0]!.serverClose(4409));
    // The client reconnects at once, on the next timer turn.
    act(() => fake.advance(0));
    expect(fake.sockets[1]!.url).toBe('wss://hive.test' + PATH + '?after=ep_7');
  });

  it('reports the stream state in the connection slice and clears it on unmount', async () => {
    const { fake, store, view } = await mounted();
    expect(store.getSnapshot().connection.views[PATH]?.state).toBe('connecting');
    fake.sockets[0]!.open();
    act(() => fake.sockets[0]!.receive(episodeFrame()));
    expect(store.getSnapshot().connection.views[PATH]?.state).toBe('live');
    view.unmount();
    expect(store.getSnapshot().connection.views).toEqual({});
  });

  it('records a refusal as the stream state and does not retry', async () => {
    const { fake, store } = await mounted();
    fake.sockets[0]!.open();
    act(() => fake.sockets[0]!.serverClose(4403));
    expect(store.getSnapshot().connection.views[PATH]?.state).toBe('refused');
    expect(fake.sockets).toHaveLength(1);
  });
});
