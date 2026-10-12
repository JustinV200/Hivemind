/**
 * `GoalControls`: confirm or decline a spoken goal before anything runs.
 *
 * A spoken goal is echoed back before anything is spent (landing-board.md section 8.4): its state
 * is `AWAITING_CONFIRMATION` until the human confirms or declines it, so a misheard sentence never
 * runs. While it waits this shows the two buttons; after the answer it says in words where the
 * goal stands. The buttons post to `POST /v1/goals/{id}/confirm` and `.../decline`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/voice/`. Drawn by `spoken_card.tsx`; calls
 *   `ChatActions.decide`.
 *
 * Key invariants:
 *   - The buttons exist only while the goal waits for the human, and are disabled while a request
 *     is on its way.
 *   - A refusal (including a step-up that was cancelled) is shown here and the goal stays held.
 *
 * See Also:
 *   - docs/entrance/landing-board.md sections 7 and 8.4.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { ErrorNotice, IdToken } from '../../../components';
import type { GoalView } from '../slice';
import type { ChatActions } from '../writes/use_actions';

const STATE_WORDS: Record<GoalView['state'], string> = {
  RECEIVED: 'Sent to the Queen as a task. She plans it on her next tick.',
  AWAITING_CONFIRMATION: 'This is a task. Nothing runs until you confirm it.',
  PLANNING: 'Confirmed. The Queen is planning it.',
  PLANNED: 'Planned.',
  REFUSED: 'Not run.',
};

export function GoalControls(props: { goal: GoalView; actions: ChatActions }): ReactElement {
  const { goal, actions } = props;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function decide(verdict: 'confirm' | 'decline'): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await actions.decide(goal.id, verdict);
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  }

  return (
    <div className="chat-form">
      <p>{STATE_WORDS[goal.state]}</p>
      {goal.goal_id ? (
        <p>
          Goal <IdToken id={goal.goal_id} />
        </p>
      ) : null}
      {goal.needs_confirmation ? (
        <div className="chat-row">
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => void decide('confirm')}
          >
            Confirm
          </button>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => void decide('decline')}
          >
            Decline
          </button>
        </div>
      ) : null}
      {error ? <ErrorNotice error={error} /> : null}
    </div>
  );
}
