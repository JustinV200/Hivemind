/**
 * `useEnrolActions()`: every write the Enrol view makes, as one set of functions.
 *
 * The writes are mint an invite, withdraw it, approve a pending device (name, capabilities, spend
 * cap, expiry), deny one, and answer a task asking to use the running screen or audio (ADR-0069:
 * allow or refuse, `answerWatch`). Each runs through `guarded`, so a step-up is asked for and the call
 * retried once, and each folds its result into the slice at once. A failure is kept in `error`
 * (shown through `ErrorNotice`) and a success leaves one plain sentence in `notice`. When the Hive
 * runs its own certificate authority an approval can answer with a sealed certificate bundle; it is
 * handed back once as `bundle` so the screen can show its passphrase, and kept nowhere.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/`. Called by `enrol_screen.tsx`, which hands the set
 *   to the invite panel, the pending list and the watch list. Every route is loopback-only: the
 *   screen that calls them is drawn only on the Hive Stand.
 *
 * Key invariants:
 *   - These are the only requests the view makes besides its read; a test lists them.
 *   - Every function resolves to true or false and never throws, so a form can close on success.
 *   - The bundle's passphrase is held in memory until the human dismisses it, then dropped.
 *
 * See Also:
 *   - src/views/devices/step_up/use_step_up.ts for the step-up flow.
 *   - docs/entrance/landing-board.md section 4.
 */
import { useMemo, useState } from 'react';
import type { HttpClient } from '../../landing_board/client';
import type { components } from '../../landing_board/types';
import { useHive } from '../../store';
import type { StepUp } from '../devices';
import type { EnrolEvent } from './slice';

type Schemas = components['schemas'];
export type ApprovalBody = Schemas['ApprovalBody'];
type BundleView = Schemas['BundleView'];

// The Entrance's own default reason, which the generated type asks a client to send.
const DEFAULT_REASON = 'denied by the operator';

// A certificate bundle the approval issued, with the name of the device it is for.
export interface IssuedBundle extends BundleView {
  device: string;
}

export interface EnrolActions {
  busy: boolean;
  error: unknown;
  notice: string | null;
  bundle: IssuedBundle | null;
  dismissBundle(): void;
  mint(label: string): Promise<boolean>;
  withdraw(deviceId: string): Promise<boolean>;
  approve(deviceId: string, body: ApprovalBody): Promise<boolean>;
  deny(deviceId: string, reason: string): Promise<boolean>;
  answerWatch(requestId: string, approve: boolean, title: string): Promise<boolean>;
}

// What every write needs: the client, a way to fold an event into the slice, and `run`.
interface Env {
  http: HttpClient;
  fold: (event: EnrolEvent) => void;
  run: <T>(call: () => Promise<T>, done: (answer: T) => string) => Promise<boolean>;
  keep: (bundle: IssuedBundle | null) => void;
}

type Writes = Omit<EnrolActions, 'busy' | 'error' | 'notice' | 'bundle' | 'dismissBundle'>;

function writesOf(env: Env): Writes {
  const { http, run, fold } = env;
  const door = (deviceId: string) => ({ params: { device_id: deviceId } });
  return {
    mint: (label) =>
      run(
        () => http.call('post', '/v1/entrance/invites', { body: { label } }),
        (invite) => {
          fold({ type: 'minted', invite });
          return `Invite ready for ${label}. It works once.`;
        },
      ),
    withdraw: (deviceId) =>
      run(
        () => http.call('delete', '/v1/entrance/invites/{device_id}', door(deviceId)),
        () => {
          fold({ type: 'withdrawn' });
          return 'Invite withdrawn.';
        },
      ),
    approve: (deviceId, body) =>
      run(
        () =>
          http.call('post', '/v1/entrance/pending/{device_id}/approve', {
            ...door(deviceId),
            body,
          }),
        (approved) => {
          fold({ type: 'decided', id: deviceId });
          if (approved.bundle) env.keep({ ...approved.bundle, device: approved.name });
          return `Approved ${approved.name}.`;
        },
      ),
    deny: (deviceId, reason) =>
      run(
        () =>
          http.call('post', '/v1/entrance/pending/{device_id}/deny', {
            ...door(deviceId),
            body: { reason: reason === '' ? DEFAULT_REASON : reason },
          }),
        (denied) => {
          fold({ type: 'decided', id: deviceId });
          return `Denied ${denied.name}.`;
        },
      ),
    answerWatch: watchAnswerOf(env),
  };
}

// ADR-0069: allow or refuse one task's use of the running screen or audio; the Queen settles it.
function watchAnswerOf(env: Env): Writes['answerWatch'] {
  return (requestId, approve, title) =>
    env.run(
      () =>
        env.http.call(
          'post',
          approve
            ? '/v1/entrance/watch/{request_id}/approve'
            : '/v1/entrance/watch/{request_id}/deny',
          { params: { request_id: requestId } },
        ),
      () => (approve ? `Allowed ${title}.` : `Refused ${title}; it will not run.`),
    );
}

export function useEnrolActions(step: StepUp): EnrolActions {
  const { client, store } = useHive();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [bundle, setBundle] = useState<IssuedBundle | null>(null);
  const { guarded } = step;

  const writes = useMemo<Writes>(() => {
    async function run<T>(call: () => Promise<T>, done: (answer: T) => string): Promise<boolean> {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        setNotice(done(await guarded(call)));
        return true;
      } catch (failure) {
        setError(failure);
        return false;
      } finally {
        setBusy(false);
      }
    }
    const fold = (event: EnrolEvent): void => store.dispatch({ slice: 'enrol', event });
    return writesOf({ http: client.http, fold, run, keep: setBundle });
  }, [client, store, guarded]);

  return { ...writes, busy, error, notice, bundle, dismissBundle: () => setBundle(null) };
}
