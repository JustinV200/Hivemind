/**
 * The login screen: device id and password, then the passkey prompt.
 *
 * Login is two factors (landing-board.md section 5). The human names the device (remembered from
 * last time) and types the operator's password; submitting runs the client's ceremony, which asks
 * the browser for the device's passkey, then opens the session. This screen is shown whenever the
 * session slice is not `active`, including after a session ended, in which case it also shows why
 * in one sentence. A refusal is shown through `ErrorNotice` and never says which factor failed.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/login/`. Rendered by `src/app.tsx` in place of the
 *   shell. Calls `client.session.login` and dispatches the result to the session slice.
 *
 * Key invariants:
 *   - The password lives only in this component's state while it is typed, and is cleared as
 *     soon as the attempt finishes, whether it worked or not.
 *   - The form cannot be submitted twice at once.
 *
 * See Also:
 *   - docs/observation/design.md section 8 for the error wording.
 *   - src/landing_board/client/session.ts for the ceremony.
 */
import { useEffect, useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../components';
import { useHive, useSlice } from '../../store';

interface Fields {
  deviceId: string;
  password: string;
}

function useRememberedDevice(setFields: (update: (f: Fields) => Fields) => void): void {
  const { client } = useHive();
  useEffect(() => {
    let current = true;
    // Prefill only if the person has not started typing a different device by the time it loads.
    void client.session.rememberedDevice().then((deviceId) => {
      if (current && deviceId) {
        setFields((f) => (f.deviceId ? f : { ...f, deviceId }));
      }
    });
    return () => {
      current = false;
    };
  }, [client, setFields]);
}

function LoginForm(props: {
  fields: Fields;
  setFields: (fields: Fields) => void;
  error: unknown;
  busy: boolean;
  onSubmit: (event: FormEvent) => void;
}): ReactElement {
  const { fields, setFields } = props;
  return (
    <form className="login-card" onSubmit={props.onSubmit}>
      <h1>Log in to the Observation Hive</h1>
      <label>
        Device
        <input
          name="device"
          autoComplete="username"
          required
          value={fields.deviceId}
          onChange={(event) => setFields({ ...fields, deviceId: event.target.value })}
        />
      </label>
      <label>
        Password
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={fields.password}
          onChange={(event) => setFields({ ...fields, password: event.target.value })}
        />
      </label>
      {props.error ? <ErrorNotice error={props.error} /> : null}
      <button type="submit" className="button" disabled={props.busy}>
        {props.busy ? 'Waiting for your passkey' : 'Log in'}
      </button>
    </form>
  );
}

export function LoginScreen(): ReactElement {
  const { client, store } = useHive();
  const { notice } = useSlice('session');
  const [fields, setFields] = useState<Fields>({ deviceId: '', password: '' });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  useRememberedDevice(setFields);

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
      setFields((f) => ({ ...f, password: '' }));
      setBusy(false);
    }
  };

  return (
    <main className="login" id="main">
      <div className="login-stack">
        {notice ? <p className="login-notice">{notice}</p> : null}
        <LoginForm
          fields={fields}
          setFields={setFields}
          error={error}
          busy={busy}
          onSubmit={(event) => void submit(event)}
        />
      </div>
    </main>
  );
}
