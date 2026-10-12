/**
 * `useReading()`: open one item in the reading pane and close it again.
 *
 * Opening an item puts it in the slice at once (so the pane appears and says it is reading) and
 * reads `GET /v1/honey/items/{item_id}`; the answer, or a refusal (403) or a gone item (404), goes
 * into the slice for the pane to draw. Closing clears the pane. Nothing here writes to the Hive.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Used by `honey_screen.tsx`; reads through the
 *   Landing Board client from the store's provider and writes only the `honey` slice.
 *
 * Key invariants:
 *   - One GET per click on an item; there is no re-read and no timer.
 *   - A reply that lands after the reader opened another item, or closed the pane, is dropped by
 *     the reducer, which matches the reply's id to the open one.
 *
 * See Also:
 *   - src/views/honey/slice/reduce.ts for the events dispatched here.
 */
import { useCallback } from 'react';
import { useHive } from '../../../store';

export interface Reading {
  open: (id: string) => void;
  close: () => void;
}

export function useReading(): Reading {
  const { client, store } = useHive();

  const open = useCallback(
    (id: string) => {
      store.dispatch({ slice: 'honey', event: { type: 'read_item', id } });
      client.http
        .call('get', '/v1/honey/items/{item_id}', { params: { item_id: id } })
        .then((item) => store.dispatch({ slice: 'honey', event: { type: 'item', id, item } }))
        .catch((error: unknown) =>
          store.dispatch({ slice: 'honey', event: { type: 'item_failed', id, error } }),
        );
    },
    [client, store],
  );

  const close = useCallback(
    () => store.dispatch({ slice: 'honey', event: { type: 'close_item' } }),
    [store],
  );

  return { open, close };
}
