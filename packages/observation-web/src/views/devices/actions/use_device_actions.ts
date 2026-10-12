/**
 * `useDeviceActions()`: every write the Devices view makes, as one set of functions.
 *
 * The writes are lock, unlock, revoke and replace-capabilities on a device, reduce and reopen on the
 * Entrance, and confirm or decline on a held request. Each runs through `guarded`, so a step-up is
 * asked for and the call retried once, and each folds the record the Entrance answered with into
 * the slice at once, so the screen does not wait for the next security frame. A failure is kept in
 * `error` (shown through `ErrorNotice`) and a success leaves one plain sentence in `notice`. The
 * loopback-only calls are made only by controls the screen draws on the Hive Stand; the Entrance
 * refuses them anywhere else regardless.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/actions/`. Called by `devices_screen.tsx`, which
 *   hands the set to the device list and the Entrance panel.
 *
 * Key invariants:
 *   - These are the only requests the view makes besides its reads; a test lists them.
 *   - Every function resolves to true or false and never throws, so a form can close on success.
 *   - Only one write runs at a time; `busy` is true meanwhile.
 *
 * See Also:
 *   - src/views/devices/step_up/use_step_up.ts for the step-up flow.
 *   - docs/entrance/landing-board.md sections 4, 7 and the device routes in the OpenAPI document.
 */
import { useMemo, useState } from 'react';
import type { HttpClient } from '../../../landing_board/client';
import { useHive } from '../../../store';
import type { DeviceRecord, DevicesEvent, HeldRecord } from '../slice';
import type { StepUp } from '../step_up';

export interface DeviceActions {
  busy: boolean;
  error: unknown;
  notice: string | null;
  clear(): void;
  lock(device: DeviceRecord): Promise<boolean>;
  unlock(device: DeviceRecord): Promise<boolean>;
  revoke(device: DeviceRecord, cancelGoals: boolean): Promise<boolean>;
  widen(device: DeviceRecord, capabilities: string[], spendCap: number | null): Promise<boolean>;
  reduce(): Promise<boolean>;
  reopen(): Promise<boolean>;
  confirm(held: HeldRecord): Promise<boolean>;
  decline(held: HeldRecord): Promise<boolean>;
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;

// What a revocation did, in one plain sentence: goals cancelled, and any left running.
function revocationNotice(name: string, cancelled: number, left: readonly string[]): string {
  const parts = [`Revoked ${name}.`];
  if (cancelled > 0) parts.push(`Cancelled ${plural(cancelled, 'open goal')}.`);
  if (left.length > 0) {
    parts.push(`${plural(left.length, 'goal')} still running: ${left.join(', ')}.`);
  }
  return parts.join(' ');
}

// What every write needs: the client, a way to fold an event into the slice, and `run`.
interface Env {
  http: HttpClient;
  fold: (event: DevicesEvent) => void;
  run: <T>(call: () => Promise<T>, done: (answer: T) => string) => Promise<boolean>;
}

type Writes = Omit<DeviceActions, 'busy' | 'error' | 'notice' | 'clear'>;

// Writes the answer's device into the slice, then returns the sentence to show.
const settled = (env: Env, device: DeviceRecord, text: string): string => {
  env.fold({ type: 'device_changed', device });
  return text;
};

function deviceWrites(env: Env): Pick<Writes, 'lock' | 'unlock' | 'revoke' | 'widen'> {
  const { http, run } = env;
  const door = (device: DeviceRecord) => ({ params: { device_id: device.id } });
  return {
    lock: (device) =>
      run(
        () => http.call('post', '/v1/devices/{device_id}/lock', door(device)),
        (answer) => settled(env, answer, `Locked ${device.name}.`),
      ),
    unlock: (device) =>
      run(
        () => http.call('post', '/v1/devices/{device_id}/unlock', door(device)),
        (answer) => settled(env, answer, `Unlocked ${device.name}.`),
      ),
    revoke: (device, cancelGoals) =>
      run(
        () =>
          http.call('post', '/v1/devices/{device_id}/revoke', {
            ...door(device),
            body: { cancel_goals: cancelGoals },
          }),
        (answer) =>
          settled(
            env,
            answer.device,
            revocationNotice(device.name, answer.goals_cancelled.length, answer.goals_left_running),
          ),
      ),
    widen: (device, capabilities, spendCap) =>
      run(
        () =>
          http.call('post', '/v1/devices/{device_id}/capabilities', {
            ...door(device),
            body: { capabilities, spend_cap_usd_per_day: spendCap },
          }),
        (answer) => settled(env, answer, `Saved what ${device.name} may do.`),
      ),
  };
}

function entranceWrites(env: Env): Pick<Writes, 'reduce' | 'reopen' | 'confirm' | 'decline'> {
  const { http, run, fold } = env;
  const held = (record: HeldRecord) => ({ params: { pending_id: record.id } });
  return {
    reduce: () =>
      run(
        () => http.call('post', '/v1/entrance/reduce'),
        (answer) => {
          fold({ type: 'mode_changed', mode: answer.mode });
          return 'The Entrance now answers on the Hive Stand only.';
        },
      ),
    reopen: () =>
      run(
        () => http.call('post', '/v1/entrance/open'),
        (answer) => {
          fold({ type: 'mode_changed', mode: answer.mode });
          return 'The Entrance is open again.';
        },
      ),
    confirm: (record) =>
      run(
        () => http.call('post', '/v1/entrance/confirmations/{pending_id}/confirm', held(record)),
        (answer) => {
          fold({ type: 'held_done', id: record.id });
          return answer.goal_request_id
            ? `Confirmed. The goal request ${answer.goal_request_id} is committed.`
            : 'Confirmed.';
        },
      ),
    decline: (record) =>
      run(
        () => http.call('post', '/v1/entrance/confirmations/{pending_id}/cancel', held(record)),
        () => {
          fold({ type: 'held_done', id: record.id });
          return 'Declined.';
        },
      ),
  };
}

export function useDeviceActions(step: StepUp): DeviceActions {
  const { client, store } = useHive();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { guarded } = step;

  const writes = useMemo<Writes>(() => {
    // Runs one write under the step-up guard; `done` folds the answer and words the success.
    async function run<T>(call: () => Promise<T>, done: (answer: T) => string): Promise<boolean> {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const answer = await guarded(call);
        setNotice(done(answer));
        return true;
      } catch (failure) {
        setError(failure);
        return false;
      } finally {
        setBusy(false);
      }
    }
    const fold = (event: DevicesEvent): void => store.dispatch({ slice: 'devices', event });
    const env: Env = { http: client.http, fold, run };
    return { ...deviceWrites(env), ...entranceWrites(env) };
  }, [client, store, guarded]);

  const clear = (): void => {
    setError(null);
    setNotice(null);
  };
  return { ...writes, busy, error, notice, clear };
}
