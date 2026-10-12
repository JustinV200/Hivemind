/**
 * `useSearch(path)`: search the open folder and page through the hits.
 *
 * `submit` starts a fresh search (the slice shows it as pending) and reads
 * `GET /v1/honey/search?path=&q=`; `more` reads the page after the hits' cursor; `clear` drops the
 * results. A refusal or failure goes into the search state, so the panel shows it through
 * `ErrorNotice`. Nothing here writes to the Hive.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Used by `honey_screen.tsx`; reads through the
 *   Landing Board client from the store's provider and writes only the `honey` slice.
 *
 * Key invariants:
 *   - One GET per search and per click on More; a search is never repeated on its own.
 *   - A reply for another query, another folder or an already-taken cursor is dropped by the
 *     reducer.
 *   - One More request at a time.
 *
 * See Also:
 *   - src/views/honey/slice/reduce.ts for the events dispatched here.
 */
import { useCallback, useState } from 'react';
import { useHive } from '../../../store';
import type { SearchState } from '../slice';

// The route's default page of hits.
export const HITS_PER_PAGE = 10;

export interface Search {
  submit: (q: string) => void;
  more: () => void;
  clear: () => void;
  loadingMore: boolean;
}

export function useSearch(path: string, current: SearchState | null): Search {
  const { client, store } = useHive();
  const [loadingMore, setLoadingMore] = useState(false);

  // One page of hits for `q`, continuing after `cursor` (null for the first page).
  const read = useCallback(
    (q: string, cursor: string | null) =>
      client.http
        .call('get', '/v1/honey/search', {
          query: { path, q, cursor, limit: HITS_PER_PAGE },
        })
        .then((page) =>
          store.dispatch({ slice: 'honey', event: { type: 'found', path, q, cursor, page } }),
        )
        .catch((error: unknown) =>
          store.dispatch({ slice: 'honey', event: { type: 'search_failed', path, q, error } }),
        ),
    [client, store, path],
  );

  const submit = useCallback(
    (q: string) => {
      const words = q.trim();
      if (words === '') return;
      store.dispatch({ slice: 'honey', event: { type: 'search', path, q: words } });
      void read(words, null);
    },
    [store, path, read],
  );

  const more = useCallback(() => {
    if (!current || current.nextCursor === null || loadingMore) return;
    setLoadingMore(true);
    void read(current.q, current.nextCursor).finally(() => setLoadingMore(false));
  }, [current, loadingMore, read]);

  const clear = useCallback(
    () => store.dispatch({ slice: 'honey', event: { type: 'clear_search' } }),
    [store],
  );

  return { submit, more, clear, loadingMore };
}
