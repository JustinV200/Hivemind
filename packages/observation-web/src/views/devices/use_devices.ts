/**
 * `useDevices()`: seed the Devices slice and keep it live from the Entrance's security stream.
 *
 * On mount it opens the slice and reads the device list, the Entrance's mode and the held requests.
 * Each security frame (an enrolment, an approval, a lockout, a reduction) folds into the slice as a
 * count and is the cue to read the three again, once a second at most (see `live/`). A read that
 * fails goes into the slice under its own name, so a device that may read the list but not the held
 * requests still gets its list.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/`. Used by `devices_screen.tsx`; reads through the
 *   Landing Board client from the store's provider and writes only the `devices` slice.
 *
 * Key invariants:
 *   - After the seed, a read happens only because a security frame arrived; there is no polling.
 *   - The three reads are independent: one refusal never hides the others' answers.
 *
 * See Also:
 *   - src/views/devices/live/use_burst_read.ts for the throttle.
 *   - docs/observation/design.md section 5.10.
 */
import { useCallback, useEffect } from 'react';
import { useHive, useSlice, useViewStream } from '../../store';
import { SECURITY_STREAM, useBurstRead } from './live';
import type { DevicesEvent, DevicesState, SecurityFrame } from './slice';

// Module-level so the stream is opened once per mount, not once per render.
const fromSecurity = (frame: SecurityFrame): DevicesEvent => ({ type: 'security', frame });

export function useDevices(): DevicesState {
  const { client, store } = useHive();
  const state = useSlice('devices');

  useViewStream(SECURITY_STREAM, 'devices', fromSecurity);

  // One round of reads; each answer or refusal goes into the slice on its own.
  const read = useCallback(() => {
    const { http } = client;
    const dispatch = (event: DevicesEvent): void => store.dispatch({ slice: 'devices', event });
    http
      .call('get', '/v1/devices')
      .then((list) => dispatch({ type: 'devices', devices: list.devices }))
      .catch((error: unknown) => dispatch({ type: 'failed', read: 'devices', error }));
    http
      .call('get', '/v1/entrance/mode')
      .then((mode) => dispatch({ type: 'mode', mode }))
      .catch((error: unknown) => dispatch({ type: 'failed', read: 'mode', error }));
    http
      .call('get', '/v1/entrance/confirmations')
      .then((list) => dispatch({ type: 'held', held: list.confirmations }))
      .catch((error: unknown) => dispatch({ type: 'failed', read: 'confirmations', error }));
  }, [client, store]);

  // The seed: forget frames counted on an earlier visit, then read once.
  useEffect(() => {
    store.dispatch({ slice: 'devices', event: { type: 'open' } });
    read();
  }, [store, read]);

  useBurstRead(state.ticks, read);
  return state;
}
