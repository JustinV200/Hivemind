/**
 * The Devices slice: the enrolled devices, the Entrance's mode and the requests held for a person.
 *
 * The view reads `GET /v1/devices`, `GET /v1/entrance/mode` and `GET /v1/entrance/confirmations`
 * once, then again after a burst of security events on `/v1/entrance/stream`. A frame carries one
 * event, not the new list, so the reducer only counts frames (`ticks`, the hook's cue to read
 * again). The writes the view makes (lock, unlock, revoke, widen, reduce, reopen, confirm, cancel)
 * answer with the record they changed, and the reducer folds that at once so the screen does not
 * wait for the next read. Each of the three reads fails on its own: a device without
 * `entrance:submit` cannot read the held requests but can still read the list.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/`. Registered in `src/app.tsx`'s table, filled
 *   by `use_devices.ts` and the action hook, read by `devices_screen.tsx`.
 *
 * Key invariants:
 *   - Pure and bounded: at most `MAX_DEVICES` devices and `MAX_HELD` held requests.
 *   - Stored records are the generated types as received; nothing is added to them here.
 *   - A read replaces its list whole, so a frame can never leave a stale row behind.
 *   - `ticks` counts frames since the view opened; it is reset on open so a visit never owes a
 *     read for frames counted on an earlier one.
 *
 * See Also:
 *   - docs/observation/design.md section 5.10.
 *   - src/store/registry.ts, merged into below so `useSlice('devices')` is typed.
 */
import type { components } from '../../landing_board/types';
import type { Slice } from '../../store';

type Schemas = components['schemas'];
export type DeviceRecord = Schemas['DeviceView'];
export type ModeRecord = Schemas['ModeView'];
export type HeldRecord = Schemas['ConfirmationView'];
export type SecurityFrame = Schemas['SecurityFrame'];

export const MAX_DEVICES = 500;
export const MAX_HELD = 200;

// Which of the three reads a failure belongs to.
export type ReadName = 'devices' | 'mode' | 'confirmations';

export interface DevicesState {
  // True once the device list has been read at least once.
  seeded: boolean;
  devices: readonly DeviceRecord[];
  mode: ModeRecord | null;
  // Null until read, and again when the read was refused.
  held: readonly HeldRecord[] | null;
  errors: Readonly<Record<ReadName, unknown>>;
  ticks: number;
}

export type DevicesEvent =
  | { type: 'open' }
  | { type: 'devices'; devices: readonly DeviceRecord[] }
  | { type: 'mode'; mode: ModeRecord }
  | { type: 'held'; held: readonly HeldRecord[] }
  | { type: 'failed'; read: ReadName; error: unknown }
  | { type: 'security'; frame: SecurityFrame }
  | { type: 'device_changed'; device: DeviceRecord }
  | { type: 'mode_changed'; mode: ModeRecord['mode'] }
  | { type: 'held_done'; id: string };

const NO_ERRORS: DevicesState['errors'] = { devices: null, mode: null, confirmations: null };

export const initialDevices: DevicesState = {
  seeded: false,
  devices: [],
  mode: null,
  held: null,
  errors: NO_ERRORS,
  ticks: 0,
};

// A read that worked clears only its own failure.
function healed(state: DevicesState, read: ReadName): DevicesState['errors'] {
  return state.errors[read] === null ? state.errors : { ...state.errors, [read]: null };
}

function replaced(devices: readonly DeviceRecord[], device: DeviceRecord): DeviceRecord[] {
  return devices.map((one) => (one.id === device.id ? device : one));
}

export function reduceDevices(state: DevicesState, event: DevicesEvent): DevicesState {
  switch (event.type) {
    case 'open':
      return state.ticks === 0 ? state : { ...state, ticks: 0 };
    case 'devices':
      return {
        ...state,
        seeded: true,
        devices: event.devices.slice(0, MAX_DEVICES),
        errors: healed(state, 'devices'),
      };
    case 'mode':
      return { ...state, mode: event.mode, errors: healed(state, 'mode') };
    case 'held':
      return {
        ...state,
        held: event.held.slice(0, MAX_HELD),
        errors: healed(state, 'confirmations'),
      };
    case 'failed':
      return { ...state, errors: { ...state.errors, [event.read]: event.error } };
    case 'security':
      return { ...state, ticks: state.ticks + 1 };
    case 'device_changed':
      return { ...state, devices: replaced(state.devices, event.device) };
    case 'mode_changed':
      return state.mode ? { ...state, mode: { ...state.mode, mode: event.mode } } : state;
    case 'held_done':
      return state.held
        ? { ...state, held: state.held.filter((one) => one.id !== event.id) }
        : state;
  }
}

export const devicesSlice: Slice<DevicesState, DevicesEvent> = {
  initial: initialDevices,
  reduce: reduceDevices,
};

// Tells the store's hooks about this slice, so `useSlice('devices')` and `dispatch` are typed.
declare module '../../store/registry' {
  interface SliceRegistry {
    devices: Slice<DevicesState, DevicesEvent>;
  }
}
