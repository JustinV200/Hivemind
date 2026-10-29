/**
 * The join screen: what a new device sees when it opens the Entrance with an invite.
 *
 * The operator hands over an invite (a link or a QR code). Opening it lands here, before any
 * login: the human names the device, the browser makes a passkey for it, and the invite is
 * redeemed. The device then waits for the operator to approve it at the Hive Stand. The screen
 * shows the key's fingerprint to read out so the operator approves the right device, and a "Check
 * again" button: it asks the Entrance once when tapped and never by itself. When the device is
 * approved the human goes on to log in with the password and this device's passkey.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/join/`. Shown by the login screen when the page's
 *   address carries an invite and this browser has no enrolled device yet; works on any listener.
 *
 * Key invariants:
 *   - Every card opens with the same `BrandHeader` as the login card.
 *   - No request is made until the human taps Make passkey, and none repeats without a tap.
 *   - A refusal is the error table's sentence through `ErrorNotice`; a redeemed, expired or unknown
 *     invite all read alike, because the Entrance says nothing more.
 *   - The invite code is never drawn.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 4, src/views/enrol/join/use_join.ts.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { BrandHeader, ErrorNotice } from '../../../components';
import './join.css';
import { useJoin } from './use_join';
import type { Join } from './use_join';

function NameForm(props: { join: Join }): ReactElement {
  const { join } = props;
  const [name, setName] = useState('');
  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (name.trim() !== '') void join.join(name);
  };
  return (
    <form className="login-card comb-cell" onSubmit={submit}>
      <BrandHeader />
      <h1>Join the Observation Hive</h1>
      <p>
        You have an invite. Name this device and make a passkey for it; the operator then approves
        it at the Hive Stand.
      </p>
      <label>
        Name this device
        <input
          name="device-name"
          maxLength={64}
          required
          autoComplete="off"
          placeholder="phone"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      {join.error ? <ErrorNotice error={join.error} /> : null}
      <button
        type="submit"
        className="button"
        disabled={join.phase === 'making' || name.trim() === ''}
      >
        {join.phase === 'making' ? 'Waiting for your passkey' : 'Make passkey and join'}
      </button>
    </form>
  );
}

function Waiting(props: { join: Join }): ReactElement {
  const { join } = props;
  return (
    <section className="login-card comb-cell" aria-label="Waiting for the operator">
      <BrandHeader />
      <h1>Waiting for the operator</h1>
      <p>Read this fingerprint to the operator so they approve the right device:</p>
      <p className="join-fingerprint" aria-label="Key fingerprint">
        {join.fingerprint}
      </p>
      <p className="join-note">This page does not refresh by itself. Tap the button to ask.</p>
      {join.notYet ? <p role="status">Not approved yet.</p> : null}
      {join.error ? <ErrorNotice error={join.error} /> : null}
      <button
        type="button"
        className="button"
        disabled={join.checking}
        onClick={() => void join.check()}
      >
        {join.checking ? 'Asking' : 'Check again'}
      </button>
    </section>
  );
}

export function JoinScreen(props: {
  code: string;
  // Called when the device is approved and the human goes on to log in.
  onDone: () => void;
  // Offered when this browser already has a device, to use that one instead of the invite.
  onLogin?: () => void;
}): ReactElement {
  const join = useJoin(props.code);
  if (join.phase === 'approved') {
    return (
      <section className="login-card comb-cell" aria-label="Approved">
        <BrandHeader />
        <h1>Approved</h1>
        <p>
          The operator approved this device. Log in with your password and this device's passkey.
        </p>
        <button type="button" className="button" onClick={props.onDone}>
          Go to login
        </button>
      </section>
    );
  }
  return (
    <>
      {join.phase === 'waiting' ? <Waiting join={join} /> : <NameForm join={join} />}
      {props.onLogin && join.phase !== 'waiting' ? (
        <button type="button" className="button" onClick={props.onLogin}>
          Log in with this device instead
        </button>
      ) : null}
    </>
  );
}
