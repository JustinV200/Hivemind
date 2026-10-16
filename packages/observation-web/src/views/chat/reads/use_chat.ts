/**
 * `useChat(stepUp)`: seed the Chat slice from the chat and the inbox, and re-read the inbox on a frame.
 *
 * On mount it reads the newest page of `GET /v1/chat` and `GET /v1/inbox`, once each. The chat read
 * goes through the step-up (`guarded`): a session the travel lock flagged is refused `403
 * step_up_required` on reads too, and the prompt that answers it sits above the composer, so the
 * human is asked here and the read is tried once more. A read that stood refused (the prompt was
 * cancelled) is read again, plainly, once a later step-up has gone through. After that the only
 * read it makes is the inbox, once for every live question or Alarm line the stream delivers (the
 * slice counts them in `inboxWanted`): the line says something is waiting, the inbox says what.
 * The live part itself is not here; the screen opens the chat stream with `useViewStream`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/reads/`. Called by `chat_screen.tsx` with the
 *   step-up `use_actions.ts` holds; reads through the Landing Board client from the store's
 *   provider and writes only the `chat` slice.
 *
 * Key invariants:
 *   - No timer and no polling: a read follows the mount, a frame or a step-up, never the clock.
 *   - A late answer from an older inbox read is dropped when a newer one has started.
 *   - A refusal goes into the slice (`error`, `inboxError`) and is shown, never swallowed.
 *   - The read after a step-up opens no prompt: a second refusal is the answer.
 *
 * See Also:
 *   - src/views/chat/slice/reduce.ts for `inboxWanted`.
 *   - tests/views/chat/chat_view.test.tsx, which proves no read happens without a frame.
 *   - tests/views/chat/step_up.test.tsx for the refused read.
 */
import { useEffect, useRef } from 'react';
import type { LandingBoard } from '../../../landing_board/client';
import { useHive, useSlice } from '../../../store';
import type { AppSlices, HiveStore } from '../../../store';
import { needsStepUp } from '../../devices';
import type { StepUp } from '../../devices';

// The newest lines read to start with; the stream carries everything after them.
export const SEED_LIMIT = 100;

type Read = <T>(action: () => Promise<T>) => Promise<T>;
const plain: Read = (action) => action();

// Reads the newest page through `read` and puts the answer, or the refusal, in the slice; the
// returned function drops an answer that lands after the caller moved on.
function seed(client: LandingBoard, store: HiveStore<AppSlices>, read: Read): () => void {
  let current = true;
  read(() => client.http.call('get', '/v1/chat', { query: { limit: SEED_LIMIT } }))
    .then((page) => current && store.dispatch({ slice: 'chat', event: { type: 'seeded', page } }))
    .catch((error: unknown) => {
      if (current) store.dispatch({ slice: 'chat', event: { type: 'failed', error } });
    });
  return () => {
    current = false;
  };
}

export function useChat(stepUp: StepUp): void {
  const { client, store } = useHive();
  const { inboxWanted: wanted, error } = useSlice('chat');
  const { guarded, steppedUp } = stepUp;
  const refusedForStepUp = needsStepUp(error);
  // The newest inbox read, so a slower older answer cannot overwrite it.
  const latest = useRef(0);

  // The seed, through the step-up, so a travel-locked session is asked here rather than sent away.
  useEffect(() => seed(client, store, guarded), [client, store, guarded]);

  // A read still refused for a step-up when one goes through (for a write, after the seed's own
  // prompt was cancelled) is read again, plainly: a refusal now stands, and no prompt reopens.
  useEffect(() => {
    if (steppedUp === 0 || !refusedForStepUp) return;
    return seed(client, store, plain);
  }, [client, store, steppedUp, refusedForStepUp]);

  useEffect(() => {
    // Runs on mount (the seed) and again each time the slice counts another live question or Alarm.
    const mine = ++latest.current;
    client.http
      .call('get', '/v1/inbox')
      .then((inbox) => {
        if (mine === latest.current)
          store.dispatch({ slice: 'chat', event: { type: 'inbox', inbox } });
      })
      .catch((error: unknown) => {
        if (mine === latest.current)
          store.dispatch({ slice: 'chat', event: { type: 'inbox_failed', error } });
      });
  }, [client, store, wanted]);
}
