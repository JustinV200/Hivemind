/**
 * `useReading(path, item)`: keep the reading pane on the item the address names.
 *
 * The route's `item` (`#/honey/<path>?item=<id>`) says which item is open, so an open pane can be
 * shared, comes back on reload and closes with Back. Opening an item puts it in the slice at once
 * (so the pane appears and says it is reading) and reads `GET /v1/honey/items/{item_id}`; the
 * answer, or a refusal (403) or a gone item (404), goes into the slice for the pane to draw. No
 * item in the address clears the pane. A new folder starts the slice clean, so the item is read
 * again when the path changes under it. Nothing here writes to the Hive.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Used by `honey_screen.tsx`; reads through the
 *   Landing Board client from the store's provider and writes only the `honey` slice.
 *
 * Key invariants:
 *   - One GET per item the address names; there is no re-read and no timer.
 *   - A reply that lands after the address moved to another item, or dropped it, is ignored by
 *     the reducer, which matches the reply's id to the open one.
 *
 * See Also:
 *   - src/views/honey/slice/reduce.ts for the events dispatched here.
 */
import { useEffect } from 'react';
import { useHive } from '../../../store';

export function useReading(path: string, item: string | null): void {
  const { client, store } = useHive();
  useEffect(() => {
    if (item === null) {
      store.dispatch({ slice: 'honey', event: { type: 'close_item' } });
      return;
    }
    const open = (id: string): void => {
      store.dispatch({ slice: 'honey', event: { type: 'read_item', id } });
      client.http
        .call('get', '/v1/honey/items/{item_id}', { params: { item_id: id } })
        .then((item) => store.dispatch({ slice: 'honey', event: { type: 'item', id, item } }))
        .catch((error: unknown) =>
          store.dispatch({ slice: 'honey', event: { type: 'item_failed', id, error } }),
        );
    };
    open(item);
  }, [client, store, path, item]);
}
