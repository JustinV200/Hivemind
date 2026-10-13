/**
 * A view's own stream: open one Entrance stream, fold its frames into a slice, report its state.
 *
 * The shell's feeds open the three streams the strip needs. A view that wants another (the
 * Thoughts view's follow mode) opens it here instead of calling the client itself, so every
 * stream in the app reports its state to the same place (the connection slice, which the top bar
 * reads) and ends a session the same way (a 4410 or 4411 close). The reconnect policy, including
 * resuming from the cursor on 4409, is the client's `socket.ts`; this module only wires it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Called by `useViewStream` in `react.tsx`; the end
 *   handling is shared with `feed.ts`.
 *
 * Key invariants:
 *   - The returned function closes the stream for good and clears its entry from the connection
 *     slice; closing twice is harmless.
 *   - A refusal (4401, 4403) is recorded as the stream's state and not retried.
 *   - Frames go to the store only through `dispatch`, as the slice's own events.
 *
 * See Also:
 *   - docs/adr/0049-observation-hive-dashboard-stack.md (streams and reconnect policy).
 *   - src/landing_board/client/socket.ts for the policy itself.
 */
import type { LandingBoard, StreamEnd, StreamOptions, StreamState } from '../landing_board/client';
import type { HiveStore } from './hive_store';
import type { AppSlices } from './registry';
import { SESSION_ENDED_NOTICE } from './slices/session';
import type { EventOf } from './slice';

const REDUCED_NOTICE = 'The Entrance was reduced to the Hive Stand. Log in again there.';

// 4410 and 4411 end the whole session; a refusal only marks its own stream (set by the state).
export function endSessionOnClose(
  client: LandingBoard,
  store: HiveStore<AppSlices>,
  end: StreamEnd,
): void {
  if (end.reason !== 'session_ended' && end.reason !== 'reduced') return;
  const notice = end.reason === 'reduced' ? REDUCED_NOTICE : SESSION_ENDED_NOTICE;
  // The key and token are dead; delete them so a reload does not restore a finished session.
  void client.session.forget();
  store.dispatch({ slice: 'session', event: { type: 'ended', notice } });
}

export interface ViewStreamDeps<K extends keyof AppSlices, F> {
  client: LandingBoard;
  store: HiveStore<AppSlices>;
  nowMs: () => number;
  path: string;
  slice: K;
  toEvent: (frame: F) => EventOf<AppSlices[K]>;
  resume?: StreamOptions<F>['resume'];
}

export function openViewStream<K extends keyof AppSlices & string, F>(
  deps: ViewStreamDeps<K, F>,
): () => void {
  const { client, store, nowMs, path, slice, toEvent, resume } = deps;
  const onState = (state: StreamState): void =>
    store.dispatch({ slice: 'connection', event: { type: 'view', path, state, at: nowMs() } });
  const handle = client.openStream<F>({
    path,
    onFrame: (frame) => store.dispatch({ slice, event: toEvent(frame) }),
    onState,
    onEnd: (end) => endSessionOnClose(client, store, end),
    resume,
  });
  return handle.close;
}
