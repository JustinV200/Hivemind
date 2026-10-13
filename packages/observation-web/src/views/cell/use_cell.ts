/**
 * `useCellPage(cellId)`: seed the Cell page slice and keep it live from three Entrance streams.
 *
 * On mount (and when the Cell changes) it opens the Cell in the slice and reads
 * `GET /v1/cells/{cell_id}/page`. The Cells stream and the telemetry stream fold straight into the
 * slice (see `slice.ts`). The trail stream is the cue to read again: when an event names this Cell
 * or its Warden the slice counts it, and this hook re-reads the page once, a second later, for the
 * lists that have no stream of their own (Alarms, Cell Wax, Capping). A refusal or failure of a
 * read goes into the slice, so the view shows it through `ErrorNotice`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/`. Used by `cell.tsx`; reads through the Landing
 *   Board client from the store's provider and writes only the `cell` slice.
 *
 * Key invariants:
 *   - After the seed, a read happens only because a trail frame named this Cell: no timer starts
 *     without one, and there is no polling.
 *   - A burst of matching frames inside the delay costs one read: the window opens on the first
 *     frame and later ones join it, so a busy Cell is re-read every second at most and a steady
 *     stream cannot starve the read.
 *   - Leaving the Cell cancels a pending read; a reply that lands late is dropped by the reducer.
 *
 * See Also:
 *   - src/views/cell/slice.ts for the events dispatched here.
 *   - src/store/react.tsx for `useViewStream`.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useHive, useSlice, useViewStream } from '../../store';
import type { CellFrame, CellPageEvent, CellPageState, TelemetryFrame, TrailFrame } from './slice';

export const CELLS_STREAM = '/v1/cells/stream';
export const TELEMETRY_STREAM = '/v1/telemetry/stream';
export const TRAIL_STREAM = '/v1/trail/stream';

// A page is built from the trail's folded window, so a flurry of events settles in about a second.
export const REREAD_DELAY_MS = 1000;

// Module-level so each stream is opened once per mount, not once per render.
const fromCell = (frame: CellFrame): CellPageEvent => ({ type: 'cell', frame });
const fromTelemetry = (frame: TelemetryFrame): CellPageEvent => ({ type: 'telemetry', frame });
const fromTrail = (frame: TrailFrame): CellPageEvent => ({ type: 'trail', frame });

export function useCellPage(cellId: string): CellPageState {
  const { client, store } = useHive();
  const state = useSlice('cell');
  const timer = useRef<number | null>(null);

  useViewStream(CELLS_STREAM, 'cell', fromCell);
  useViewStream(TELEMETRY_STREAM, 'cell', fromTelemetry);
  useViewStream(TRAIL_STREAM, 'cell', fromTrail);

  // One read of the page; its answer or refusal goes into the slice, which drops a stale one.
  const read = useCallback(() => {
    client.http
      .call('get', '/v1/cells/{cell_id}/page', { params: { cell_id: cellId } })
      .then((page) => {
        store.dispatch({ slice: 'cell', event: { type: 'seeded', cellId, page } });
      })
      .catch((error: unknown) => {
        store.dispatch({ slice: 'cell', event: { type: 'failed', cellId, error } });
      });
  }, [client, store, cellId]);

  // The seed: open the Cell clean, then read it once.
  useEffect(() => {
    store.dispatch({ slice: 'cell', event: { type: 'open', cellId } });
    read();
  }, [store, cellId, read]);

  // The re-read: the first matching trail frame opens a window, later ones join it.
  const ticks = state.trailTicks;
  useEffect(() => {
    if (ticks === 0 || timer.current !== null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      read();
    }, REREAD_DELAY_MS);
  }, [ticks, read]);

  // Leaving (or switching Cell) cancels a window that is still open.
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
    },
    [cellId],
  );

  return state;
}
