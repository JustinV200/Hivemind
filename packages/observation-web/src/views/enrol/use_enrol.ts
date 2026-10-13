/**
 * `useEnrol()`: seed the Enrol slice and keep the pending list live from the security stream.
 *
 * On mount it opens the slice and reads `GET /v1/entrance/pending`. Each security frame (a device
 * redeemed an invite, another decision was made on another screen) is the cue to read again, once
 * a second at most, and never without a frame (see `src/views/devices/live/`). A refusal or any
 * other failure of the read goes into the slice and is shown through `ErrorNotice`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/`. Used by `enrol_screen.tsx`; reads through the
 *   Landing Board client from the store's provider and writes only the `enrol` slice.
 *
 * Key invariants:
 *   - After the seed, a read happens only because a security frame arrived; there is no polling.
 *   - This hook is mounted only on the Hive Stand, so it never reads a loopback-only list elsewhere.
 *
 * See Also:
 *   - src/views/devices/use_devices.ts, the same pattern for the device list.
 */
import { useCallback, useEffect } from 'react';
import { useHive, useSlice, useViewStream } from '../../store';
import { SECURITY_STREAM, useBurstRead } from '../devices';
import type { EnrolEvent, EnrolState, SecurityFrame } from './slice';

// Module-level so the stream is opened once per mount, not once per render.
const fromSecurity = (frame: SecurityFrame): EnrolEvent => ({ type: 'security', frame });

export function useEnrol(): EnrolState {
  const { client, store } = useHive();
  const state = useSlice('enrol');

  useViewStream(SECURITY_STREAM, 'enrol', fromSecurity);

  const read = useCallback(() => {
    const dispatch = (event: EnrolEvent): void => store.dispatch({ slice: 'enrol', event });
    client.http
      .call('get', '/v1/entrance/pending')
      .then((list) => dispatch({ type: 'pending', devices: list.devices }))
      .catch((error: unknown) => dispatch({ type: 'failed', error }));
  }, [client, store]);

  // The seed: forget frames counted on an earlier visit, then read once.
  useEffect(() => {
    store.dispatch({ slice: 'enrol', event: { type: 'open' } });
    read();
  }, [store, read]);

  useBurstRead(state.ticks, read);
  return state;
}
