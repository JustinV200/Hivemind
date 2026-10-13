/**
 * `useChat()`: seed the Chat slice from the chat and the inbox, and re-read the inbox on a frame.
 *
 * On mount it reads the newest page of `GET /v1/chat` and `GET /v1/inbox`, once each. After that
 * the only read it makes is the inbox, once for every live question or Alarm line the stream
 * delivers (the slice counts them in `inboxWanted`): the line says something is waiting, the inbox
 * says what. The live part itself is not here; the screen opens the chat stream with
 * `useViewStream`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/reads/`. Called by `chat_screen.tsx`; reads through
 *   the Landing Board client from the store's provider and writes only the `chat` slice.
 *
 * Key invariants:
 *   - No timer and no polling: a read follows the mount or a frame, never the clock.
 *   - A late answer from an older inbox read is dropped when a newer one has started.
 *   - A refusal goes into the slice (`error`, `inboxError`) and is shown, never swallowed.
 *
 * See Also:
 *   - src/views/chat/slice/reduce.ts for `inboxWanted`.
 *   - tests/views/chat/chat_view.test.tsx, which proves no read happens without a frame.
 */
import { useEffect, useRef } from 'react';
import { useHive, useSlice } from '../../../store';

// The newest lines read to start with; the stream carries everything after them.
export const SEED_LIMIT = 100;

export function useChat(): void {
  const { client, store } = useHive();
  const wanted = useSlice('chat').inboxWanted;
  // The newest inbox read, so a slower older answer cannot overwrite it.
  const latest = useRef(0);

  useEffect(() => {
    let current = true;
    client.http
      .call('get', '/v1/chat', { query: { limit: SEED_LIMIT } })
      .then((page) => current && store.dispatch({ slice: 'chat', event: { type: 'seeded', page } }))
      .catch((error: unknown) => {
        if (current) store.dispatch({ slice: 'chat', event: { type: 'failed', error } });
      });
    return () => {
      current = false;
    };
  }, [client, store]);

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
