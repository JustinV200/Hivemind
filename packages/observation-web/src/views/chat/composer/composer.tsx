/**
 * `Composer`: the text box, the Send button, the goal switch and push-to-talk.
 *
 * Typing a message and pressing Send (or Ctrl/Cmd+Enter) posts it to `POST /v1/chat`, which puts it
 * in the Queen's inbox; the text shows at once as pending in the conversation and the box clears.
 * Send is the only filled accent button in the app (design.md section 5.6), so the one write
 * path is unlike every navigation control. Beside it is the hold-to-talk button, which sends a
 * clip instead. A chat message never starts a task by itself: "Request as a goal" (off by default)
 * turns the same box into a goal request, the button then reads "Request goal", and the words go
 * to `POST /v1/goals` through `useGoal`; a line under the switch says that difference on screen,
 * since a first-timer who types "build me X" and presses Send would otherwise wait for nothing.
 * The box keeps its words until the request is accepted.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/composer/`. Drawn by `chat_screen.tsx` in the dock
 *   that sticks to the bottom of the view, above the nav on a phone.
 *
 * Key invariants:
 *   - An empty or all-space message is not sendable; the text is trimmed before it is sent.
 *   - The box limit is the schema's (16000 characters).
 *   - It sends through `ChatActions.send`, which never throws: a refusal becomes a failed pending
 *     message in the conversation. A goal request that is refused stays in the box, with the
 *     refusal shown under it.
 *   - The switch starts off on every visit; a goal is never requested by accident.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6.
 *   - src/views/chat/voice/push_to_talk.tsx for the spoken half.
 */
import { useState } from 'react';
import type { FormEvent, KeyboardEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import { PushToTalk } from '../voice/push_to_talk';
import { useVoice } from '../voice/use_voice';
import type { QuestionView } from '../slice';
import type { ChatActions } from '../writes/use_actions';
import { useGoal } from '../writes/use_goal';
import type { StepUp } from '../writes/use_step_up';

// ChatPost.text maxLength in the published document.
const MAX_MESSAGE = 16000;

export function Composer(props: {
  actions: ChatActions;
  questions: readonly QuestionView[];
  guarded: StepUp['guarded'];
}): ReactElement {
  const { actions } = props;
  const [text, setText] = useState('');
  const [asGoal, setAsGoal] = useState(false);
  const voice = useVoice(actions, props.questions);
  const goal = useGoal(props.guarded);
  const words = text.trim();

  function send(): void {
    if (words === '' || goal.busy) return;
    if (asGoal) {
      // A goal clears the box only once the Entrance accepted it, so a refusal keeps the words.
      void goal.request(words).then((accepted) => {
        if (accepted) setText('');
      });
      return;
    }
    // The box clears at once; the words live on as a pending message until their line arrives.
    setText('');
    void actions.send(words);
  }

  function submit(event: FormEvent): void {
    event.preventDefault();
    send();
  }

  function onKeyDown(event: KeyboardEvent): void {
    // Plain Enter is a new line; Ctrl or Cmd with Enter sends.
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send();
    }
  }

  return (
    <form
      className="chat-composer comb-cell comb-cell--strong"
      onSubmit={submit}
      aria-label="Message the Queen"
    >
      <label>
        <span className="sr-only">Message to the Queen</span>
        <textarea
          rows={2}
          placeholder={
            asGoal
              ? 'Describe the task for the Queen'
              : 'Ask the Queen for something, or ask her a question'
          }
          maxLength={MAX_MESSAGE}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
        />
      </label>
      <label className="chat-switch">
        <input
          type="checkbox"
          role="switch"
          checked={asGoal}
          onChange={(event) => setAsGoal(event.target.checked)}
        />
        <span>Request as a goal</span>
      </label>
      <p className="chat-hint">
        A message only talks to the Queen. A goal asks her to plan and run work; she may ask you to
        confirm first.
      </p>
      {asGoal && goal.error ? <ErrorNotice error={goal.error} /> : null}
      <div className="chat-row">
        <button type="submit" className="chat-send" disabled={words === '' || goal.busy}>
          {asGoal ? 'Request goal' : 'Send'}
        </button>
        <PushToTalk voice={voice} />
      </div>
    </form>
  );
}
