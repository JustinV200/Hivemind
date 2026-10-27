/**
 * `SpokenCard`: what the Entrance heard, and what it did with it.
 *
 * After a clip is sent the card shows the transcript, so the human can see a mishearing. For a
 * goal it adds the confirm and decline controls while the goal is held; for an answer it says the
 * task runs again; for chat it says the words went to the Queen. Only the device that spoke ever
 * receives the transcript, so it is shown here and nowhere else. Dismiss clears the card.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/voice/`. Drawn by `chat_screen.tsx` in the dock,
 *   from the slice's `spoken` and `goals`.
 *
 * Key invariants:
 *   - The goal shown is the slice's latest view of it, so a confirm updates the card in place.
 *   - Nothing is drawn when nothing was spoken.
 *
 * See Also:
 *   - src/views/chat/slice/reduce.ts for how a voice result folds.
 */
import type { ReactElement } from 'react';
import { goalById } from '../slice';
import type { ChatState } from '../slice';
import type { ChatActions } from '../writes/use_actions';
import { GoalControls } from './goal_controls';

export function SpokenCard(props: {
  state: ChatState;
  actions: ChatActions;
  onDismiss: () => void;
}): ReactElement | null {
  const { state, actions } = props;
  if (!state.spoken) return null;
  const { result } = state.spoken;
  const goal = result.goal ? (goalById(state, result.goal.id) ?? result.goal) : null;
  return (
    <section className="chat-spoken comb-cell comb-cell--strong" aria-label="What was heard">
      <p>
        You said: <q className="chat-transcript">{result.transcript}</q>
      </p>
      {goal ? <GoalControls key={goal.id} goal={goal} actions={actions} /> : null}
      {result.answered ? <p>Answered. The task it was waiting on runs again.</p> : null}
      {result.chat ? <p>Sent to the Queen.</p> : null}
      <div className="chat-row">
        <button type="button" className="button" onClick={props.onDismiss}>
          Dismiss
        </button>
      </div>
    </section>
  );
}
