/**
 * The login screen: device id and password, then the passkey prompt; or the join screen.
 *
 * Login is two factors (landing-board.md section 5). The human names the device (remembered from
 * last time) and types the operator's password; submitting runs the client's ceremony, which asks
 * the browser for the device's passkey, then opens the session. This screen is shown whenever the
 * session slice is not `active`, including after a session ended, in which case it also shows why
 * in one sentence. When the page is opened with an invite in its address and this browser has no
 * enrolled device yet, it shows the join screen first (a new device makes its passkey and waits for
 * approval), and goes on to this form with the new device id filled in once the operator approves.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/login/`. Rendered by `src/app.tsx` in place of the
 *   shell. Calls `client.session.login` and dispatches the result to the session slice.
 *
 * Key invariants:
 *   - The password lives only in this component's state while it is typed, and is cleared as
 *     soon as the attempt finishes, whether it worked or not.
 *   - The form cannot be submitted twice at once.
 *   - The join screen appears only when an invite is in the address; a browser that already knows
 *     a device sees the login form, with a button to join instead.
 *
 * See Also:
 *   - docs/observation/design.md section 8 for the error wording.
 *   - src/landing_board/client/session.ts for the ceremony, src/views/enrol/join/ for joining.
 */
import { useCallback, useEffect, useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { useHive, useSlice } from '../../store';
import { inviteCodeFrom, JoinScreen } from '../enrol/join';
import { LoginForm } from './login_form';
import type { Fields } from './login_form';

// The device id this browser enrolled or last logged in with: undefined while it is being read.
function useRemembered(): { remembered: string | null | undefined; reload: () => void } {
  const { client } = useHive();
  const [remembered, setRemembered] = useState<string | null | undefined>(undefined);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let current = true;
    void client.session.rememberedDevice().then((deviceId) => {
      if (current) setRemembered(deviceId);
    });
    return () => {
      current = false;
    };
  }, [client, round]);
  return { remembered, reload: useCallback(() => setRound((n) => n + 1), []) };
}

interface Typed {
  deviceId: string | null;
  password: string;
}

// Runs the login ceremony for the typed fields; the password is cleared whatever the outcome.
function useLogin(fields: Fields, setTyped: (update: (t: Typed) => Typed) => void) {
  const { client, store } = useHive();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await client.session.login(fields.deviceId.trim(), fields.password);
      store.dispatch({ slice: 'session', event: { type: 'opened', session } });
    } catch (failure) {
      setError(failure);
    } finally {
      // The password is gone from the page as soon as the attempt is over.
      setTyped((t) => ({ ...t, password: '' }));
      setBusy(false);
    }
  };
  return { error, busy, submit };
}

export function LoginScreen(): ReactElement {
  const { notice } = useSlice('session');
  // What the person typed; a null device id means they have not touched it, so the remembered one shows.
  const [typed, setTyped] = useState<Typed>({ deviceId: null, password: '' });
  // The invite in the page's address, until it is spent; read once so a later hash change cannot swap it.
  const [invite, setInvite] = useState(() => inviteCodeFrom(window.location));
  // The human's own choice between the two screens; null leaves it to what the browser knows.
  const [choice, setChoice] = useState<'join' | 'login' | null>(null);
  const { remembered, reload } = useRemembered();
  // Prefilled from the remembered device until the person types a different one.
  const fields: Fields = { deviceId: typed.deviceId ?? remembered ?? '', password: typed.password };
  const login = useLogin(fields, setTyped);

  // With an invite, wait to learn whether this browser knows a device before choosing a screen.
  const waiting = invite !== null && remembered === undefined;
  const joining =
    invite !== null && (choice ?? (remembered === null ? 'join' : 'login')) === 'join';
  return (
    <main className="login" id="main">
      <div className="login-stack">
        {notice ? <p className="login-notice">{notice}</p> : null}
        {waiting ? (
          <p>Opening the Observation Hive</p>
        ) : joining ? (
          <JoinScreen
            code={invite}
            onLogin={remembered ? () => setChoice('login') : undefined}
            onDone={() => {
              // The invite is spent and the new device id is stored: log in with it.
              setInvite(null);
              setChoice('login');
              reload();
            }}
          />
        ) : (
          <LoginForm
            fields={fields}
            setFields={setTyped}
            error={login.error}
            busy={login.busy}
            onSubmit={(event) => void login.submit(event)}
            onJoin={invite ? () => setChoice('join') : undefined}
          />
        )}
      </div>
    </main>
  );
}
