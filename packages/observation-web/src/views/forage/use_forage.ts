/**
 * `useForage()`: seed the Forage slice from the three ledger reads and keep it live from the stream.
 *
 * On mount it opens the slice afresh and sends `GET /v1/forage`, `/v1/wardens` and `/v1/llm`
 * together; the answers land in one `seeded` event, so the view never draws a half-read ledger. A
 * refusal (the device lacks `observe`) or any failure goes into the slice, and the view shows it
 * through `ErrorNotice`. The live part is the forage stream, folded frame by frame. The one later
 * read is a reaction to a frame: a `forage.capacity_reported` frame carries no capacity figures, so
 * the ledger is read again when the slice counts one. There is no timer and no polling. The host
 * cards are named from the Fleet slice, so when no view has seeded it yet the fleet is read once
 * here too (`useFleetNames`), the one write outside the `forage` slice.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/`. Used by `forage_screen.tsx`; reads through the
 *   Landing Board client from the store's provider and writes the `forage` slice, plus the
 *   `fleet` slice's seed when the fleet has not been read.
 *
 * Key invariants:
 *   - Without a frame, the only requests are the three of the seed, plus one read of the fleet
 *     when no view has seeded it yet (the host cards' names).
 *   - A reply that lands after the reader left is dropped.
 *   - Pass module-level functions to `useViewStream`, so the stream opens once per mount.
 *
 * See Also:
 *   - src/views/forage/slice/reduce.ts for the events dispatched here.
 */
import { useEffect, useRef } from 'react';
import { useHive, useSlice, useViewStream } from '../../store';
import type { ForageEvent, ForageFrame, ForageState } from './slice';

export const FORAGE_STREAM = '/v1/forage/stream';

// Module-level so the stream is opened once per mount, not once per render.
const toEvent = (frame: ForageFrame): ForageEvent => ({ type: 'frame', frame });

export function useForage(): ForageState {
  const { client, store } = useHive();
  const state = useSlice('forage');
  useViewStream(FORAGE_STREAM, 'forage', toEvent);
  useFleetNames();

  // The seed: the three reads go out together and are folded as one answer.
  useEffect(() => {
    let current = true;
    store.dispatch({ slice: 'forage', event: { type: 'open' } });
    Promise.all([
      client.http.call('get', '/v1/forage'),
      client.http.call('get', '/v1/wardens'),
      client.http.call('get', '/v1/llm'),
    ])
      .then(([forage, wardens, llm]) => {
        if (current)
          store.dispatch({ slice: 'forage', event: { type: 'seeded', forage, wardens, llm } });
      })
      .catch((error: unknown) => {
        if (current) store.dispatch({ slice: 'forage', event: { type: 'failed', error } });
      });
    return () => {
      current = false;
    };
  }, [client, store]);

  // A capacity report moves figures no frame carries: read the ledger once more per report seen.
  const ticks = state.capacityTicks;
  const readAt = useRef(ticks);
  useEffect(() => {
    if (ticks === readAt.current) return;
    readAt.current = ticks;
    client.http
      .call('get', '/v1/forage')
      .then((forage) => store.dispatch({ slice: 'forage', event: { type: 'reseeded', forage } }))
      .catch((error: unknown) =>
        store.dispatch({ slice: 'forage', event: { type: 'failed', error } }),
      );
  }, [client, store, ticks]);

  return state;
}

// The host cards take their names from the Fleet slice, which only the Fleet view seeds; until
// the fleet has been read once, read it here so the cards are named on the first load.
function useFleetNames(): void {
  const { client, store } = useHive();
  const fleet = useSlice('fleet');
  useEffect(() => {
    if (fleet.seeded) return;
    let current = true;
    client.http
      .call('get', '/v1/fleet')
      .then((answer) => {
        if (current) store.dispatch({ slice: 'fleet', event: { type: 'seeded', answer } });
      })
      .catch(() => {
        // Names are a nicety: a host card shows its id when the fleet cannot be read, and the
        // Fleet view will report the failure itself when it is opened.
      });
    return () => {
      current = false;
    };
  }, [client, store, fleet.seeded]);
}
