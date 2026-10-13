/**
 * `StepUpPrompt` and `StepUpPanel`: the two places a human is asked to confirm again.
 *
 * `StepUpPrompt` is the in-place question a refused write opens (it reads the state `useStepUp`
 * holds and draws nothing when no question is open). `StepUpPanel` is the standing form the shell's
 * banner points to: while the session slice says `needsStepUp` (the travel lock saw a new network)
 * it asks for both factors before anything else, and goes away when the flag clears.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/devices/step_up/`. Drawn by the Devices and Enrol views;
 *   the panel calls `client.session.stepUp` itself and tells the session slice when it worked.
 *
 * Key invariants:
 *   - The panel renders nothing unless the session needs a step-up.
 *   - A wrong password leaves the panel open with the refusal shown, and never ends the session.
 *   - Neither keeps the password beyond the one call.
 *
 * See Also:
 *   - src/shell/step_up_banner.tsx, which links here.
 *   - docs/entrance/landing-board.md section 7.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { useHive, useSlice } from '../../../store';
import { StepUpForm } from './step_up_form';
import type { StepUp } from './use_step_up';

export function StepUpPrompt(props: { step: StepUp }): ReactElement | null {
  const { ask, submit, cancel } = props.step;
  if (!ask) return null;
  return <StepUpForm key={ask.round} failure={ask.failure} onSubmit={submit} onCancel={cancel} />;
}

export function StepUpPanel(): ReactElement | null {
  const { client, store } = useHive();
  const { needsStepUp } = useSlice('session');
  const [failure, setFailure] = useState<unknown>(null);
  const [round, setRound] = useState(0);
  if (!needsStepUp) return null;

  const confirm = (password: string): void => {
    client.session
      .stepUp(password)
      .then(() => store.dispatch({ slice: 'session', event: { type: 'stepped_up' } }))
      .catch((error: unknown) => {
        // A fresh round remounts the form, so the typed password is gone before the next try.
        setFailure(error);
        setRound((n) => n + 1);
      });
  };
  return <StepUpForm key={round} failure={failure} onSubmit={confirm} />;
}
