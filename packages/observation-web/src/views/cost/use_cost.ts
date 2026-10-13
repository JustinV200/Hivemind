/**
 * `useCost()`: seed the Cost slice from `GET /v1/cost` and keep it live from the trail stream.
 *
 * On mount it opens the slice afresh and reads the cost once. The live part is the trail stream:
 * each `llm.call` frame with usage is folded into the goal, slot and provider it belongs to at once
 * (see the reducer), so the figures move as the Hive spends. The one later read is a reaction to a
 * frame: the first folded call of a burst opens a one-second window, and when it closes the cost is
 * read again, which puts calls under their goal where the Entrance can attribute them and corrects
 * any drift. There is no timer without a frame and no polling.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/`. Used by `cost_screen.tsx`; reads through the
 *   Landing Board client from the store's provider and writes only the `cost` slice.
 *
 * Key invariants:
 *   - Without a folded call, the only request is the seed.
 *   - A burst of calls inside the window costs one read: the window opens on the first call and
 *     later ones join it, so a busy Hive is read every second at most and a steady stream cannot
 *     starve the read.
 *   - Leaving the view cancels a window that is still open and drops a reply that lands late.
 *   - Pass module-level functions to `useViewStream`, so the stream opens once per mount.
 *
 * See Also:
 *   - src/views/cost/slice/reduce.ts for the events dispatched here.
 *   - src/views/cell/use_cell.ts, which reads the Cell page on the same rule.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useHive, useSlice, useViewStream } from '../../store';
import type { CostEvent, CostState, TrailFrame } from './slice';

export const TRAIL_STREAM = '/v1/trail/stream';

// A burst of calls settles in about a second, so the cost is read once per burst.
export const REREAD_DELAY_MS = 1000;

// Module-level so the stream is opened once per mount, not once per render.
const toEvent = (frame: TrailFrame): CostEvent => ({ type: 'frame', frame });

// The re-read: the first folded call opens a window, later ones join it, and leaving cancels it.
function useRereadWindow(ticks: number, read: () => void): void {
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
    [],
  );
}

export function useCost(): CostState {
  const { client, store } = useHive();
  const state = useSlice('cost');
  // False once the view is left, so a reply that lands late writes nothing.
  const mounted = useRef(true);
  useViewStream(TRAIL_STREAM, 'cost', toEvent);

  // One read of the cost; its answer or refusal goes into the slice.
  const read = useCallback(() => {
    client.http
      .call('get', '/v1/cost')
      .then((cost) => {
        if (mounted.current) store.dispatch({ slice: 'cost', event: { type: 'read', cost } });
      })
      .catch((error: unknown) => {
        if (mounted.current) store.dispatch({ slice: 'cost', event: { type: 'failed', error } });
      });
  }, [client, store]);

  // The seed: open clean, then read once.
  useEffect(() => {
    mounted.current = true;
    store.dispatch({ slice: 'cost', event: { type: 'open' } });
    read();
    return () => {
      mounted.current = false;
    };
  }, [store, read]);

  useRereadWindow(state.ticks, read);

  return state;
}
