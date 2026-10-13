/**
 * `useThoughts(bee)`: seed the Thoughts slice for one bee and page back through its history.
 *
 * On mount (and when the bee changes) it opens the principal in the slice and reads the newest
 * page of `GET /v1/thoughts`. `loadOlder` reads the page after the slice's cursor. A refusal (the
 * device lacks `observe:thoughts`) or any other failure goes into the slice, so the view shows it
 * through `ErrorNotice`. The live part is not here: the view opens the episodes stream with
 * `useViewStream`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Used by `thoughts_view.tsx`; reads through
 *   the Landing Board client from the store's provider and writes only the `thoughts` slice.
 *
 * Key invariants:
 *   - Reads once per bee and once per click on Older; there is no timer and no polling.
 *   - A reply that lands after the reader moved to another bee is dropped by the reducer.
 *   - One Older request at a time.
 *
 * See Also:
 *   - src/views/thoughts/slice.ts for the events dispatched here.
 */
import { useCallback, useEffect, useState } from 'react';
import { useHive, useSlice } from '../../store';
import type { ThoughtsState } from './slice';

export const FIRST_PAGE = 50;

export interface Thoughts {
  state: ThoughtsState;
  loadingOlder: boolean;
  loadOlder: () => void;
}

export function useThoughts(bee: string): Thoughts {
  const { client, store } = useHive();
  const state = useSlice('thoughts');
  const [loadingOlder, setLoadingOlder] = useState(false);

  useEffect(() => {
    let current = true;
    store.dispatch({ slice: 'thoughts', event: { type: 'open', principal: bee } });
    client.http
      .call('get', '/v1/thoughts', { query: { principal: bee, limit: FIRST_PAGE } })
      .then((page) => {
        if (current)
          store.dispatch({ slice: 'thoughts', event: { type: 'seeded', principal: bee, page } });
      })
      .catch((error: unknown) => {
        if (current)
          store.dispatch({ slice: 'thoughts', event: { type: 'failed', principal: bee, error } });
      });
    return () => {
      current = false;
    };
  }, [client, store, bee]);

  const cursor = state.nextCursor;
  const loadOlder = useCallback(() => {
    if (cursor === null || loadingOlder) return;
    setLoadingOlder(true);
    client.http
      .call('get', '/v1/thoughts', { query: { principal: bee, cursor, limit: FIRST_PAGE } })
      .then((page) => {
        store.dispatch({
          slice: 'thoughts',
          event: { type: 'older', principal: bee, cursor, page },
        });
      })
      .catch((error: unknown) => {
        store.dispatch({ slice: 'thoughts', event: { type: 'failed', principal: bee, error } });
      })
      .finally(() => setLoadingOlder(false));
  }, [client, store, bee, cursor, loadingOlder]);

  return { state, loadingOlder, loadOlder };
}
