/**
 * `useWaitingNotice()`: keep an ongoing notification up while a question is waiting.
 *
 * With a session active, the hook reads the inbox once (questions already waiting), then follows the
 * live push stream, whose notices say only that a question is waiting or was withdrawn. The count of
 * waiting questions drives the platform's waiting notification: shown while it is above zero,
 * withdrawn when it reaches zero, so a question answered on another device clears it here too. The
 * notice never carries the question; the human opens the Chat view and the app fetches it.
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
 *
 * See Also:
 *   - src/share/waiting.ts for the reducer, and docs/entrance/landing-board.md section 9.
 */
import { useEffect } from 'react';
import type { WaitingNotifier } from '../../landing_board/client';
import { useHive, useSlice } from '../../store';
import { applyNotice, NOTHING_WAITING, seeded } from './waiting';
import type { PushNotice, WaitingState } from './waiting';

export function useWaitingNotice(notifier: WaitingNotifier): void {
  const { client } = useHive();
  const { status } = useSlice('session');

  useEffect(() => {
    if (status !== 'active') return;
    let state: WaitingState = NOTHING_WAITING;
    let shown = 0;
    let live = true;
    // Tells the platform only when the number of waiting questions changed.
    const publish = (): void => {
      const count = state.refs.size;
      if (count === shown) return;
      shown = count;
      void (count > 0 ? notifier.show(count) : notifier.clear()).catch(() => undefined);
    };
    const stream = client.openStream<PushNotice>({
      path: '/v1/push/stream',
      onFrame: (notice) => {
        state = applyNotice(state, notice);
        publish();
      },
      onState: () => undefined,
      onEnd: () => undefined,
    });
    client.http
      .call('get', '/v1/inbox')
      .then((inbox) => {
        if (!live) return;
        const waiting = seeded(inbox.questions.map((question) => question.id));
        state = { refs: new Set([...waiting.refs, ...state.refs]), seen: state.seen };
        publish();
      })
      .catch(() => undefined);
    return () => {
      live = false;
      stream.close();
      void notifier.clear().catch(() => undefined);
    };
  }, [client, status, notifier]);
}
