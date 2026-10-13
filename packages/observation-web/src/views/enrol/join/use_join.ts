/**
 * `useJoin()`: the join flow of a new device, as a small state machine.
 *
 * Four phases: `form` (name the device), `making` (the browser's passkey prompt and the redemption
 * are in flight), `waiting` (redeemed; the operator has not decided yet) and `approved`. The only
 * way out of `waiting` is the human asking: `check` asks the Entrance once whether the device has
 * been approved, and nothing else does, so there is no polling and no timer. A refusal at any step
 * is kept for display and the phase stays where it was, so the human can try again.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/join/`. Used by `join_screen.tsx`; calls
 *   `client.session.enrol` and `client.session.approved`, which are the only requests it makes.
 *
 * Key invariants:
 *   - A spent invite is taken out of the page's address as soon as it is redeemed.
 *   - `check` never says why a device is not approved; the Entrance does not, so neither does this.
 *   - Nothing here touches `fetch` or WebAuthn: both are behind the Landing Board client.
 *
 * See Also:
 *   - src/landing_board/client/session.ts for `enrol` and `approved`.
 */
import { useState } from 'react';
import { useHive } from '../../../store';
import { describeThisDevice } from './describe_device';
import { leaveInvite } from './invite_code';

export type JoinPhase = 'form' | 'making' | 'waiting' | 'approved';

export interface JoinState {
  phase: JoinPhase;
  fingerprint: string | null;
  // True after a check that found the device still waiting.
  notYet: boolean;
  checking: boolean;
  error: unknown;
}

export interface Join extends JoinState {
  join(name: string): Promise<void>;
  check(): Promise<void>;
}

const FORM: JoinState = {
  phase: 'form',
  fingerprint: null,
  notYet: false,
  checking: false,
  error: null,
};

export function useJoin(code: string): Join {
  const { client } = useHive();
  const [state, setState] = useState<JoinState>(FORM);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const patch = (change: Partial<JoinState>): void => setState((now) => ({ ...now, ...change }));

  async function join(name: string): Promise<void> {
    patch({ phase: 'making', error: null });
    try {
      const enrolled = await client.session.enrol(code, describeThisDevice(name));
      leaveInvite();
      setDeviceId(enrolled.deviceId);
      patch({ phase: 'waiting', fingerprint: enrolled.fingerprint });
    } catch (error) {
      patch({ phase: 'form', error });
    }
  }

  async function check(): Promise<void> {
    if (deviceId === null) return;
    patch({ checking: true, error: null, notYet: false });
    try {
      const approved = await client.session.approved(deviceId);
      patch(approved ? { phase: 'approved', checking: false } : { notYet: true, checking: false });
    } catch (error) {
      patch({ error, checking: false });
    }
  }

  return { ...state, join, check };
}
