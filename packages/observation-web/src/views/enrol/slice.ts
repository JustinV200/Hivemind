/**
 * The Enrol slice: the invite just minted and the devices waiting for the operator to decide.
 *
 * The view reads `GET /v1/entrance/pending` once, then again after a burst of security events on
 * `/v1/entrance/stream` (a frame says something changed, not what). The invite is the answer of
 * `POST /v1/entrance/invites`, which the Entrance shows once; the slice keeps it, in memory only,
 * so leaving the screen and coming back inside its fifteen minutes does not lose the code. When the
 * device it admits shows up in the pending list the invite is spent, and the slice drops it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/`. Registered in `src/app.tsx`'s table, filled by
 *   `use_enrol.ts` and the action hook, read by `enrol_screen.tsx`.
 *
 * Key invariants:
 *   - Pure and bounded: at most `MAX_PENDING` waiting devices.
 *   - Only devices in the `PENDING` state are kept; an invited device has not asked yet.
 *   - The invite code never leaves memory: it is not written to any store or log.
 *   - `ticks` counts frames since the view opened and is reset on open, as in the Devices slice.
 *
 * See Also:
 *   - docs/observation/design.md section 5.10.
 *   - src/store/registry.ts, merged into below so `useSlice('enrol')` is typed.
 */
import type { components } from '../../landing_board/types';
import type { Slice } from '../../store';

type Schemas = components['schemas'];
export type PendingDevice = Schemas['DeviceView'];
export type InviteRecord = Schemas['InviteView'];
export type SecurityFrame = Schemas['SecurityFrame'];

export const MAX_PENDING = 200;

export interface EnrolState {
  seeded: boolean;
  pending: readonly PendingDevice[];
  invite: InviteRecord | null;
  error: unknown;
  ticks: number;
}

export type EnrolEvent =
  | { type: 'open' }
  | { type: 'pending'; devices: readonly PendingDevice[] }
  | { type: 'failed'; error: unknown }
  | { type: 'security'; frame: SecurityFrame }
  | { type: 'minted'; invite: InviteRecord }
  | { type: 'withdrawn' }
  | { type: 'decided'; id: string };

export const initialEnrol: EnrolState = {
  seeded: false,
  pending: [],
  invite: null,
  error: null,
  ticks: 0,
};

function listed(state: EnrolState, devices: readonly PendingDevice[]): EnrolState {
  const waiting = devices.filter((d) => d.status === 'PENDING').slice(0, MAX_PENDING);
  // A device waiting under the invite's id means the invite was redeemed and is spent.
  const spent = state.invite && waiting.some((d) => d.id === state.invite?.device_id);
  return {
    ...state,
    seeded: true,
    pending: waiting,
    error: null,
    invite: spent ? null : state.invite,
  };
}

export function reduceEnrol(state: EnrolState, event: EnrolEvent): EnrolState {
  switch (event.type) {
    case 'open':
      return state.ticks === 0 ? state : { ...state, ticks: 0 };
    case 'pending':
      return listed(state, event.devices);
    case 'failed':
      return { ...state, error: event.error };
    case 'security':
      return { ...state, ticks: state.ticks + 1 };
    case 'minted':
      return { ...state, invite: event.invite };
    case 'withdrawn':
      return state.invite ? { ...state, invite: null } : state;
    case 'decided':
      return { ...state, pending: state.pending.filter((d) => d.id !== event.id) };
  }
}

export const enrolSlice: Slice<EnrolState, EnrolEvent> = {
  initial: initialEnrol,
  reduce: reduceEnrol,
};

// Tells the store's hooks about this slice, so `useSlice('enrol')` and `dispatch` are typed.
declare module '../../store/registry' {
  interface SliceRegistry {
    enrol: Slice<EnrolState, EnrolEvent>;
  }
}
