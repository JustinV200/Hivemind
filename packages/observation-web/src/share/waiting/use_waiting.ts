/**
 * `useWaitingNotice()`: keep an ongoing notification up while a question is waiting.
 *
 * With a session active, the hook reads the inbox (questions already waiting) and follows the live
 * push stream, whose notices say only that a question is waiting or was withdrawn. The stream keeps
 * no replay: a notice sent while the socket was down (a network drop, the backoff after it) is
 * gone for this device, so the inbox is read again each time the stream comes back and its answer
 * replaces the set, with the notices heard meanwhile laid over it. The count of waiting questions
 * drives the platform's waiting notification: shown while it is above zero, withdrawn when it
 * reaches zero, so a question answered on another device clears it here too. The notice never
 * carries the question; the human opens the Chat view and the app fetches it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/share/`. Mounted by `phone_host.tsx` only where the platform
 *   offers a `WaitingNotifier` (the Android shell).
 *
 * Key invariants:
 *   - Nothing runs without an active session, and everything is closed and cleared when it ends.
 *   - The notifier is called only when the count changes, and its failures are ignored: a missing
 *     notification permission must not break the app.
 *   - A refused inbox read or a refused stream (a device without the capability) is silent.
 *   - Only the newest inbox read counts: an older one still out when the stream reconnects again
 *     is dropped when it answers, so a stale answer never overwrites a fresher one.
 *
 * See Also:
 *   - src/share/waiting.ts for the reducer, and docs/entrance/landing-board.md section 9.
 */
import { useEffect } from 'react';
import type { LandingBoard, WaitingNotifier } from '../../landing_board/client';
import { useHive, useSlice } from '../../store';
import { applyNotice, NOTHING_WAITING, reseeded } from './waiting';
import type { PushNotice, WaitingState } from './waiting';

// Tells the platform only when the number of waiting questions changed; its failures are ignored.
function publisher(notifier: WaitingNotifier): (count: number) => void {
  let shown = 0;
  return (count) => {
    if (count === shown) return;
    shown = count;
    void (count > 0 ? notifier.show(count) : notifier.clear()).catch(() => undefined);
  };
}

// One session's follow: the inbox reads, the stream and the count; returns the function that stops it.
function follow(client: LandingBoard, notifier: WaitingNotifier): () => void {
  let state: WaitingState = NOTHING_WAITING;
  let live = true;
  // The notices heard since the newest inbox read went out; null while no read is out.
  let since: PushNotice[] | null = null;
  const publish = publisher(notifier);
  // Reads the inbox and lets it replace the set; a read a newer one overtook is dropped.
  const reseed = (): void => {
    const mine: PushNotice[] = [];
    since = mine;
    client.http
      .call('get', '/v1/inbox')
      .then((inbox) => {
        if (!live || since !== mine) return;
        since = null;
        state = reseeded(
          state,
          inbox.questions.map((question) => question.id),
          mine,
        );
        publish(state.refs.size);
      })
      .catch(() => {
        if (since === mine) since = null;
      });
  };
  const stream = client.openStream<PushNotice>({
    path: '/v1/push/stream',
    onFrame: (notice) => {
      since?.push(notice);
      state = applyNotice(state, notice);
      publish(state.refs.size);
    },
    // Each time the stream is accepted, what was missed while it was down is in the inbox.
    onState: (streamState) => {
      if (streamState === 'live') reseed();
    },
    onEnd: () => undefined,
  });
  // The first read goes out at once, so a question already waiting is shown before the hello
  // deadline passes; the stream's first `live` reads again, covering the gap between the two.
  reseed();
  return () => {
    live = false;
    stream.close();
    void notifier.clear().catch(() => undefined);
  };
}

export function useWaitingNotice(notifier: WaitingNotifier): void {
  const { client } = useHive();
  const { status } = useSlice('session');

  useEffect(() => {
    if (status !== 'active') return;
    return follow(client, notifier);
  }, [client, status, notifier]);
}
