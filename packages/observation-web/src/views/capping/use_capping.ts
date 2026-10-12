/**
 * `useCapping()`: seed the Capping slice and keep it live from the trail stream.
 *
 * On mount it opens the slice and reads `GET /v1/capping` once. The trail stream folds each
 * `capping.*` frame into the slice at once (see `slice/`), and each such frame is also the cue to
 * read again: the first frame of a burst opens a one second window and one read closes it, so the
 * counts the frames cannot carry are settled without a read per frame. A refusal (the device lacks
 * `observe`) or any other failure of a read goes into the slice, so the view shows it through
 * `ErrorNotice`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/`. Used by `capping_screen.tsx`; reads through
 *   the Landing Board client from the store's provider and writes only the `capping` slice.
 *
 * Key invariants:
 *   - After the seed, a read happens only because a `capping.*` frame arrived: no timer starts
 *     without one, and there is no polling.
 *   - A burst of frames inside the window costs one read: later frames join the open window, so a
 *     busy Hive is re-read every second at most and a steady stream cannot starve the read.
 *   - Leaving the view cancels a window that is still open.
 *
 * See Also:
 *   - src/views/capping/slice/reduce.ts for the events dispatched here.
 *   - src/views/cell/use_cell.ts, the same pattern for one Cell.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useHive, useSlice, useViewStream } from '../../store';
import type { CappingEvent, CappingState, TrailFrame } from './slice';

export const TRAIL_STREAM = '/v1/trail/stream';

// The server folds the trail on each read, so a flurry of hops settles in about a second.
export const REREAD_DELAY_MS = 1000;

// Module-level so the stream is opened once per mount, not once per render.
const fromTrail = (frame: TrailFrame): CappingEvent => ({ type: 'trail', frame });

export function useCapping(): CappingState {
  const { client, store } = useHive();
  const state = useSlice('capping');
  const timer = useRef<number | null>(null);

  useViewStream(TRAIL_STREAM, 'capping', fromTrail);

  // One read of the queue; its answer or refusal goes into the slice.
  const read = useCallback(() => {
    client.http
      .call('get', '/v1/capping')
      .then((view) => store.dispatch({ slice: 'capping', event: { type: 'seeded', view } }))
      .catch((error: unknown) =>
        store.dispatch({ slice: 'capping', event: { type: 'failed', error } }),
      );
  }, [client, store]);

  // The seed: forget frames counted on an earlier visit, then read once.
  useEffect(() => {
    store.dispatch({ slice: 'capping', event: { type: 'open' } });
    read();
  }, [store, read]);

  // The re-read: the first capping frame opens a window, later ones join it.
  const ticks = state.ticks;
  useEffect(() => {
    if (ticks === 0 || timer.current !== null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      read();
    }, REREAD_DELAY_MS);
  }, [ticks, read]);

  // Leaving cancels a window that is still open.
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
    },
    [],
  );

  return state;
}
