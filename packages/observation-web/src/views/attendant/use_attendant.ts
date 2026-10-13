/**
 * `useAttendant(principal)`: seed the Attendant slice and keep it live from the task stream.
 *
 * On mount (and when the principal changes) it opens the principal in the slice and reads
 * `GET /v1/attendant/{principal}`. The task stream (`/v1/tasks/stream`) folds straight into the
 * slice (see `slice.ts`), which moves a node's status and placement at once. The same frames are
 * the cue to read again: the slice counts the ones that touch this principal's graph, and this
 * hook re-reads the view once, a second after the first frame of a burst, for the inbox order and
 * the finished and up-next lists that a frame does not carry. A refusal or failure of a read goes
 * into the slice, so the screen shows it through `ErrorNotice`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/`. Used by `attendant.tsx`; reads through the
 *   Landing Board client from the store's provider and writes only the `attendant` slice.
 *
 * Key invariants:
 *   - After the seed, a read happens only because a frame for this principal arrived: no timer
 *     starts without one, and there is no polling.
 *   - A burst of frames inside the delay costs one read: the window opens on the first frame and
 *     later ones join it, so a busy graph is re-read every second at most and a steady stream
 *     cannot starve the read.
 *   - Leaving the principal cancels a pending read; a reply that lands late is dropped by the
 *     reducer.
 *
 * See Also:
 *   - src/views/attendant/slice.ts for the events dispatched here.
 *   - src/store/react.tsx for `useViewStream`.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useHive, useSlice, useViewStream } from '../../store';
import type { AttendantEvent, AttendantState, TaskGraphFrame } from './slice';

export const TASKS_STREAM = '/v1/tasks/stream';

// The view orders its inbox from durable state, so a flurry of frames settles in about a second.
export const REREAD_DELAY_MS = 1000;

// Module-level so the stream is opened once per mount, not once per render.
const fromTask = (frame: TaskGraphFrame): AttendantEvent => ({ type: 'task', frame });

export function useAttendant(principal: string): AttendantState {
  const { client, store } = useHive();
  const state = useSlice('attendant');
  const timer = useRef<number | null>(null);

  useViewStream(TASKS_STREAM, 'attendant', fromTask);

  // One read of the view; its answer or refusal goes into the slice, which drops a stale one.
  const read = useCallback(() => {
    client.http
      .call('get', '/v1/attendant/{principal}', { params: { principal } })
      .then((view) => {
        store.dispatch({ slice: 'attendant', event: { type: 'seeded', principal, view } });
      })
      .catch((error: unknown) => {
        store.dispatch({ slice: 'attendant', event: { type: 'failed', principal, error } });
      });
  }, [client, store, principal]);

  // The seed: open the principal clean, then read it once.
  useEffect(() => {
    store.dispatch({ slice: 'attendant', event: { type: 'open', principal } });
    read();
  }, [store, principal, read]);

  // The re-read: the first matching frame opens a window, later ones join it.
  const ticks = state.frameTicks;
  useEffect(() => {
    if (ticks === 0 || timer.current !== null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      read();
    }, REREAD_DELAY_MS);
  }, [ticks, read]);

  // Leaving (or switching principal) cancels a window that is still open.
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
    },
    [principal],
  );

  return state;
}
