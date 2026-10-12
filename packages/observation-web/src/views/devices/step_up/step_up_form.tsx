/**
 * `StepUpForm`: the password field and the Confirm button of a step-up, with the refusal in place.
 *
 * Step-up is both factors again (design.md section 8: "This needs you to confirm again. Enter your
 * password and use your device key."). The device key is the passkey prompt the browser raises
 * when the form is submitted; the human types only the password. The words come from the client's
 * error table, so the sentence is the same everywhere. A wrong password is shown as the refusal it
 * was, and the field is cleared for another try because a new `round` remounts the form.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/step_up/`. Drawn by `StepUpPrompt` (a write that
 *   was refused) and by `StepUpPanel` (the shell banner's target); it owns only the typed text.
 *
 * Key invariants:
 *   - The field is `type="password"` with autocomplete off, and locks once submitted.
 *   - It holds no logic about the write that is waiting; it only reports a password or a cancel.
 *
 * See Also:
 *   - src/views/devices/step_up/step_up_prompt.tsx.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import { describeError, LandingBoardError } from '../../../landing_board/client';
import './step_up.css';
import { STEP_UP_CODE } from './use_step_up';

// The sentence and next step of the step-up refusal, from the error table.
const ASKING = describeError(
  new LandingBoardError(403, { error: STEP_UP_CODE, detail: 'Step-up is required.' }),
);

export function StepUpForm(props: {
  failure: unknown;
  onSubmit: (password: string) => void;
  onCancel?: () => void;
}): ReactElement {
  const [password, setPassword] = useState('');
  const [sent, setSent] = useState(false);

  function submit(event: FormEvent): void {
    event.preventDefault();
    if (password === '') return;
    // The field locks while the password is checked; a wrong one remounts this form clean.
    setSent(true);
    props.onSubmit(password);
  }

  return (
    <form className="step-up" onSubmit={submit} aria-label="Confirm again">
      <p>
        <strong>{ASKING.sentence}</strong> {ASKING.action}
      </p>
      {props.failure ? <ErrorNotice error={props.failure} /> : null}
      <label>
        Password
        <input
          type="password"
          autoComplete="off"
          value={password}
          disabled={sent}
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
      <div className="step-up-row">
        <button type="submit" className="button" disabled={sent || password === ''}>
          Confirm
        </button>
        {props.onCancel ? (
          <button type="button" className="button" onClick={props.onCancel}>
            Cancel
          </button>
        ) : null}
      </div>
    </form>
  );
}
