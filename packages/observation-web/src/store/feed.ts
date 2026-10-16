/**
 * Live feeds: seed the summary, then open the cells, telemetry and episodes streams.
 *
 * Once a session is active the shell calls `startFeeds`. It reads the summary once (so the strip
 * has numbers before any frame), reads the device's own name for the top bar, and opens the three
 * streams the strip needs. Each stream's frames go to the summary slice and each state change to
 * the connection slice. A stream ending with 4410 or 4411 ends the session; a refusal (4401,
 * 4403) is recorded and not retried. The returned function closes everything.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Called from an effect in `src/shell/shell.tsx`.
 *   Uses only the `LandingBoard` client, so tests drive it with the fake browser.
 *
 * Key invariants:
 *   - Views never poll: the only reads here are the two one-time seeds.
 *   - A failed seed read is not an error; the strip then fills from the streams alone.
 *   - Closing is idempotent and leaves no socket or timer behind, and a seed that answers after
 *     the close is dropped, so a finished session never writes into the store.
 *
 * See Also:
 *   - docs/adr/0049-observation-hive-dashboard-stack.md (streams and reconnect policy).
 *   - src/landing_board/client/socket.ts for the policy itself.
 */
import type { LandingBoard, StreamState } from '../landing_board/client';
import type { components } from '../landing_board/types';
import type { HiveStore } from './hive_store';
import type { AppSlices } from './registry';
import type { StreamName } from './slices/connection';
import { endSessionOnClose } from './view_stream';

type CellFrame = components['schemas']['CellFrame'];
type TelemetryFrame = components['schemas']['TelemetryFrame'];
type EpisodeFrame = components['schemas']['EpisodeFrame'];

export interface FeedDeps {
  client: LandingBoard;
  store: HiveStore<AppSlices>;
  nowMs: () => number;
}

async function seed(deps: FeedDeps, stillOpen: () => boolean): Promise<void> {
  const { client, store } = deps;
  // The two reads are independent, so they go out together; neither failing stops the feeds.
  const [counters, device] = await Promise.all([
    client.http.call('get', '/v1/summary').catch(() => null),
    client.http.call('get', '/v1/devices/me').catch(() => null),
  ]);
  // The shell may have gone while the reads were out; a late answer must not land in the store.
  if (!stillOpen()) return;
  if (counters) store.dispatch({ slice: 'summary', event: { type: 'seed', counters } });
  if (device) store.dispatch({ slice: 'session', event: { type: 'named', name: device.name } });
}

function open<F>(
  deps: FeedDeps,
  name: StreamName,
  path: string,
  fold: (frame: F) => void,
): () => void {
  const { store, client, nowMs } = deps;
  const onState = (state: StreamState): void =>
    store.dispatch({
      slice: 'connection',
      event: { type: 'stream', name, state, at: nowMs() },
    });
  return client.openStream<F>({
    path,
    onFrame: fold,
    onState,
    onEnd: (end) => endSessionOnClose(client, store, end),
  }).close;
}

export function startFeeds(deps: FeedDeps): () => void {
  const { store, nowMs } = deps;
  store.dispatch({ slice: 'connection', event: { type: 'reset', at: nowMs() } });
  let running = true;
  void seed(deps, () => running);
  const closers = [
    open<CellFrame>(deps, 'cells', '/v1/cells/stream', (frame) =>
      store.dispatch({ slice: 'summary', event: { type: 'cell', frame } }),
    ),
    open<TelemetryFrame>(deps, 'telemetry', '/v1/telemetry/stream', (frame) =>
      store.dispatch({ slice: 'summary', event: { type: 'telemetry', frame } }),
    ),
    open<EpisodeFrame>(deps, 'episodes', '/v1/episodes/stream', (frame) =>
      store.dispatch({ slice: 'summary', event: { type: 'episode', frame } }),
    ),
  ];
  return () => {
    running = false;
    for (const close of closers) close();
  };
}
