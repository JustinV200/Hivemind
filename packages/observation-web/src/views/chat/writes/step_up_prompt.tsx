/**
 * `StepUpPrompt`: ask the human for the password again, in place, above the composer.
 *
 * The Entrance wants both factors fresh before it does some things (design.md section 8 gives the
 * wording: "This needs you to confirm again. Enter your password and use your device key."). The
 * prompt shows that sentence from the client's error table, a password field and two buttons. A
 * wrong password is shown as the refusal it was, with the field cleared for another try.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/writes/`. Drawn by the composer's dock from the
 *   state the shared `useStepUp` (the Devices view's `step_up/`) holds; it owns only the text
 *   being typed.
 *
 * Key invariants:
 *   - The field is `type="password"` with autocomplete off, and is cleared on every new round.
 *   - It holds no logic about the write that is waiting; it only reports a password or a cancel.
 *
 * See Also:
 *   - src/views/devices/step_up/use_step_up.ts for the flow around it.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import { describeError } from '../../../landing_board/client';
import type { StepUpAsk } from '../../devices';

export function StepUpPrompt(props: {
  ask: StepUpAsk;
  onSubmit: (password: string) => void;
  onCancel: () => void;
}): ReactElement {
  const { ask } = props;
  const [password, setPassword] = useState('');
  const [sent, setSent] = useState(false);
  const words = describeError(ask.original);

  function submit(event: FormEvent): void {
    event.preventDefault();
    if (password === '') return;
    // The field locks while the password is checked; a wrong one remounts this form clean.
    setSent(true);
    props.onSubmit(password);
  }

  return (
    <form className="chat-stepup" onSubmit={submit} aria-label="Confirm again">
      <p>
        <strong>{words.sentence}</strong> {words.action}
      </p>
      {ask.failure ? <ErrorNotice error={ask.failure} /> : null}
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
      <div className="chat-row">
        <button type="submit" className="button" disabled={sent || password === ''}>
          Confirm
        </button>
        <button type="button" className="button" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
