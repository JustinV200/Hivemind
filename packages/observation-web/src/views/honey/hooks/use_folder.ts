/**
 * `useHoneyFolder(path)`: read one folder of the Honey tree, page through it, and keep it fresh.
 *
 * On mount (and when the path changes) it opens the folder in the slice and reads
 * `GET /v1/honey/tree?path=`. `loadMore` reads the page after the slice's cursor. The Honey Store
 * has no stream of its own, so the trail stream is the cue to read again: when an event concerns
 * the open folder (see `slice/fold.ts`) the slice counts it, and this hook reads the folder once
 * more, a second later, asking for as many entries as the reader has already paged in. A refusal or
 * failure goes into the slice, so the view shows it through `ErrorNotice`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/`. Used by `honey_screen.tsx`; reads through the
 *   Landing Board client from the store's provider and writes only the `honey` slice.
 *
 * Key invariants:
 *   - After the seed, a read happens only because a trail frame concerned this folder: no timer
 *     starts without one, and there is no polling.
 *   - A burst of frames inside the delay costs one read: the window opens on the first frame and
 *     later ones join it, so a busy Hive is read every second at most and a steady stream cannot
 *     starve the read.
 *   - Leaving the folder cancels a pending read; a reply that lands late is dropped by the reducer.
 *   - Every request is a GET.
 *
 * See Also:
 *   - src/views/honey/slice/reduce.ts for the events dispatched here.
 *   - src/views/cell/use_cell.ts, which re-reads the Cell page on the same rule.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useHive, useSlice, useViewStream } from '../../../store';
import type { HoneyEvent, HoneyState, TrailFrame } from '../slice';

export const TRAIL_STREAM = '/v1/trail/stream';

// A burst of Honey events settles in about a second, so the folder is read once per burst.
export const REREAD_DELAY_MS = 1000;
// The route's default page, and its cap: a re-read asks for what the reader has paged in, up to it.
export const FIRST_PAGE = 50;
export const MAX_PAGE = 200;

// Module-level so the stream is opened once per mount, not once per render.
const fromTrail = (frame: TrailFrame): HoneyEvent => ({ type: 'trail', frame });

// How many entries a re-read asks for: everything already shown, so paging in is not undone.
export function rereadLimit(state: HoneyState): number {
  const held = (state.folder?.items.length ?? 0) + (state.folder?.folders.length ?? 0);
  return Math.min(MAX_PAGE, Math.max(FIRST_PAGE, held));
}

// The re-read: the first matching frame opens a window, later ones join it, and leaving cancels it.
function useRereadWindow(ticks: number, path: string, read: () => void): void {
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (ticks === 0 || timer.current !== null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      read();
    }, REREAD_DELAY_MS);
  }, [ticks, read]);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
    },
    [path],
  );
}

export interface FolderRead {
  state: HoneyState;
  loadingMore: boolean;
  loadMore: () => void;
}

export function useHoneyFolder(path: string): FolderRead {
  const { client, store } = useHive();
  const state = useSlice('honey');
  const [loadingMore, setLoadingMore] = useState(false);

  useViewStream(TRAIL_STREAM, 'honey', fromTrail);

  // One read of the folder's first entries; its answer or refusal goes into the slice.
  const read = useCallback(() => {
    const limit = rereadLimit(store.getSnapshot().honey);
    client.http
      .call('get', '/v1/honey/tree', { query: { path, limit } })
      .then((folder) => store.dispatch({ slice: 'honey', event: { type: 'seeded', path, folder } }))
      .catch((error: unknown) =>
        store.dispatch({ slice: 'honey', event: { type: 'failed', path, error } }),
      );
  }, [client, store, path]);

  // The seed: open the folder, then read it once.
  useEffect(() => {
    store.dispatch({ slice: 'honey', event: { type: 'open', path } });
    read();
  }, [store, path, read]);

  useRereadWindow(state.trailTicks, path, read);

  const cursor = state.folder?.next_cursor ?? null;
  const loadMore = useCallback(() => {
    if (cursor === null || loadingMore) return;
    setLoadingMore(true);
    client.http
      .call('get', '/v1/honey/tree', { query: { path, cursor, limit: FIRST_PAGE } })
      .then((folder) =>
        store.dispatch({ slice: 'honey', event: { type: 'more', path, cursor, folder } }),
      )
      .catch((error: unknown) =>
        store.dispatch({ slice: 'honey', event: { type: 'failed', path, error } }),
      )
      .finally(() => setLoadingMore(false));
  }, [client, store, path, cursor, loadingMore]);

  return { state, loadingMore, loadMore };
}
