/**
 * `LoginForm`: the device id and password fields of the login screen, and the way into joining.
 *
 * Login is two factors (landing-board.md section 5): the human names the device (remembered from
 * last time) and types the operator's password; the passkey is the browser's own prompt, raised
 * when the form is submitted. When the page was opened with an invite but this browser already
 * knows a device, a second button offers the join screen instead, so a browser whose old device was
 * revoked can enrol again from a new invite.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/login/`. Drawn by `login.tsx`; it owns no state, so the
 *   screen decides what the fields hold and what a submit does.
 *
 * Key invariants:
 *   - The password field is `type="password"`; the heading and labels are the ones other tests and
 *     the shell rely on.
 *   - A refusal is shown through `ErrorNotice` and never says which factor failed.
 *
 * See Also:
 *   - src/views/login/login.tsx for the state around it.
 */
import type { FormEvent, ReactElement } from 'react';
import { BrandHeader, ErrorNotice } from '../../components';

export interface Fields {
  deviceId: string;
  password: string;
}

export function LoginForm(props: {
  fields: Fields;
  setFields: (fields: Fields) => void;
  error: unknown;
  busy: boolean;
  onSubmit: (event: FormEvent) => void;
  // Present only when the page carries an invite this browser could still use.
  onJoin?: () => void;
}): ReactElement {
  const { fields, setFields } = props;
  return (
    <form className="login-card comb-cell" onSubmit={props.onSubmit}>
      <BrandHeader />
      <h1>Log in to the Observation Hive</h1>
      <p className="login-hint">
        The device you enrolled, the operator's password, then your passkey.
      </p>
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
      {props.onJoin ? (
        <button type="button" className="button" onClick={props.onJoin}>
          Join with the invite in this link
        </button>
      ) : null}
    </form>
  );
}
