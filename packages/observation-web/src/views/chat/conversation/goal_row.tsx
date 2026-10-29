/**
 * `GoalRow`: a goal the human asked for from the chatbox, as a line in the conversation.
 *
 * It shows the words the human typed, the request's id and where it stands in words, and a link to
 * the Queen's Attendant view where the task graph the goal becomes is drawn. A goal the Entrance
 * holds for the human's yes carries the same Confirm and Decline controls a spoken goal has; a
 * goal that was committed straight away has none, because there is nothing left to decide.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `conversation.tsx` after
 *   the pending messages, from the slice's `asked` and `goals`.
 *
 * Key invariants:
 *   - The state is a word as well as a style; the controls come from `GoalControls`.
 *   - It makes no request itself; Confirm and Decline go through `ChatActions.decide`.
 *   - The link goes to the list view, since the Landing Board does not name the goal's tasks yet.
 *
 * See Also:
 *   - src/views/chat/writes/use_goal.ts for the request that creates the line.
 *   - src/views/chat/voice/goal_controls.tsx for the controls it reuses.
 */
import type { ReactElement } from 'react';
import { IdToken, TimeStamp } from '../../../components';
import { hrefFor } from '../../../router';
import { goalById } from '../slice';
import type { AskedGoal, ChatState } from '../slice';
import { GoalControls } from '../voice/goal_controls';
import type { ChatActions } from '../writes/use_actions';

export function GoalRow(props: {
  asked: AskedGoal;
  state: ChatState;
  actions: ChatActions;
}): ReactElement {
  const { asked, state, actions } = props;
  const goal = goalById(state, asked.id);
  return (
    <li className="chat-line chat-line--human chat-line--goal">
      <article aria-label="You, requested a goal">
        <header className="chat-line-head">
          <strong>You</strong>
          <span className="tag">Sent as a goal</span>
          <TimeStamp at={asked.at} />
        </header>
        <p className="chat-text">{asked.text}</p>
        <p className="chat-links">
          <a href={hrefFor({ name: 'attendant', principal: 'queen' })}>
            Request <IdToken id={asked.id} />
          </a>
        </p>
        {goal ? <GoalControls key={goal.id} goal={goal} actions={actions} /> : null}
      </article>
    </li>
  );
}
