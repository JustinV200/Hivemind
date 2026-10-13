/**
 * `useFleet()`: seed the Fleet slice from the aggregate and read it again after Cell frames.
 *
 * On mount it reads `GET /v1/fleet` once. The slice counts every frame of the Cells stream; when
 * that count moves, a read is due `rereadMs` (one second) later, and any frames that land in the
 * meantime ride on that same read, so a burst costs one request and a long run of frames costs at
 * most one a second instead of starving. A refusal or any other failure goes into the slice, so
 * the view shows it through `ErrorNotice`. The live part is not here: the view opens the Cells
 * stream with `useViewStream`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/`. Used by `fleet.tsx`; reads through the Landing
 *   Board client from the store's provider and writes only the `fleet` slice.
 *
 * Key invariants:
 *   - No read happens without a frame or a mount: there is no interval and no polling.
 *   - Only the newest read's answer is kept, so a slow old answer cannot overwrite a newer one.
 *   - Leaving the view cancels a pending read and drops an answer still in flight.
 *
 * See Also:
 *   - src/views/fleet/slice.ts for the events dispatched here and the `frames` counter.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useHive, useSlice } from '../../store';
import type { FleetState } from './slice';

export const REREAD_MS = 1000;

export function useFleet(rereadMs: number = REREAD_MS): FleetState {
  const { client, store } = useHive();
  const state = useSlice('fleet');
  // The ticket of the newest read; an answer with an older ticket has been overtaken.
  const newest = useRef(0);
  const due = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Frames already counted when the view mounted belong to an earlier visit, not to this one.
  const seen = useRef(state.frames);

  const read = useCallback(() => {
    newest.current += 1;
    const ticket = newest.current;
    client.http
      .call('get', '/v1/fleet')
      .then((answer) => {
        if (ticket === newest.current)
          store.dispatch({ slice: 'fleet', event: { type: 'seeded', answer } });
      })
      .catch((error: unknown) => {
        if (ticket === newest.current)
          store.dispatch({ slice: 'fleet', event: { type: 'failed', error } });
      });
  }, [client, store]);

  useEffect(() => {
    read();
    return () => {
      // Leaving: nothing pending may fire, and an answer still on its way is dropped.
      newest.current += 1;
      if (due.current !== null) clearTimeout(due.current);
      due.current = null;
    };
  }, [read]);

  useEffect(() => {
    if (state.frames === seen.current) return;
    seen.current = state.frames;
    // A read is already due and will see this frame too; one timer per burst.
    if (due.current !== null) return;
    due.current = setTimeout(() => {
      due.current = null;
      read();
    }, rereadMs);
  }, [state.frames, read, rereadMs]);

  return state;
}
