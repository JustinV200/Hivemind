/**
 * `Composer`: the text box, the Send button and push-to-talk.
 *
 * Typing a message and pressing Send (or Ctrl/Cmd+Enter) posts it to `POST /v1/chat`, which puts it
 * in the Queen's inbox; the text shows at once as pending in the conversation and the box clears.
 * Send is the only filled accent button in the app (design.md section 5.6), so the one write
 * path is unlike every navigation control. Beside it is the hold-to-talk button, which sends a
 * clip instead. A task is asked for in the same box: the Queen reads the message and plans.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/composer/`. Drawn by `chat_screen.tsx` in the dock
 *   that sticks to the bottom of the view, above the nav on a phone.
 *
 * Key invariants:
 *   - An empty or all-space message is not sendable; the text is trimmed before it is sent.
 *   - The box limit is the schema's (16000 characters).
 *   - It sends through `ChatActions.send`, which never throws: a refusal becomes a failed pending
 *     message in the conversation.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6.
 *   - src/views/chat/voice/push_to_talk.tsx for the spoken half.
 */
import { useState } from 'react';
import type { FormEvent, KeyboardEvent, ReactElement } from 'react';
import { PushToTalk } from '../voice/push_to_talk';
import { useVoice } from '../voice/use_voice';
import type { QuestionView } from '../slice';
import type { ChatActions } from '../writes/use_actions';

// ChatPost.text maxLength in the published document.
const MAX_MESSAGE = 16000;

export function Composer(props: {
  actions: ChatActions;
  questions: readonly QuestionView[];
}): ReactElement {
  const { actions } = props;
  const [text, setText] = useState('');
  const voice = useVoice(actions, props.questions);
  const words = text.trim();

  function send(): void {
    if (words === '') return;
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
    <form className="chat-composer" onSubmit={submit} aria-label="Message the Queen">
      <label>
        <span className="sr-only">Message to the Queen</span>
        <textarea
          rows={2}
          placeholder="Ask the Queen for something, or ask her a question"
          maxLength={MAX_MESSAGE}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
        />
      </label>
      <div className="chat-row">
        <button type="submit" className="chat-send" disabled={words === ''}>
          Send
        </button>
        <PushToTalk voice={voice} />
      </div>
    </form>
  );
}
