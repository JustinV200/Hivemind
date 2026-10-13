/**
 * `QuestionForm`: answer a question the Queen is waiting on, in place under its line.
 *
 * A question with options shows one button per option and sends the option's text with its index
 * (`chosen_option`); a question without options shows a text field and an Answer button. The
 * answer goes to `POST /v1/inbox/questions/{id}/answer`; on success the slice drops the question,
 * so this form disappears, and the task it unblocked runs again. A refusal is shown here, in place.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `line_row.tsx` and
 *   `waiting.tsx`; calls `ChatActions.answer`.
 *
 * Key invariants:
 *   - Each control is disabled while an answer is on its way, so a question is answered once.
 *   - The text field's limit is the schema's (4000), and an empty answer is not sendable.
 *
 * See Also:
 *   - docs/entrance/landing-board.md section 8.3.
 *   - src/views/chat/writes/use_actions.ts for what `answer` does.
 */
import { useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { ErrorNotice } from '../../../components';
import type { QuestionView } from '../slice';
import type { ChatActions } from '../writes/use_actions';

// AnswerBody.text maxLength in the published document.
const MAX_ANSWER = 4000;

export function QuestionForm(props: {
  question: QuestionView;
  actions: ChatActions;
}): ReactElement {
  const { question, actions } = props;
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function answer(words: string, option: number | null): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await actions.answer(question, words, option);
    } catch (failure) {
      // The question is still waiting: show why and let the human try again.
      setError(failure);
      setBusy(false);
    }
  }

  function submit(event: FormEvent): void {
    event.preventDefault();
    if (text.trim() !== '') void answer(text.trim(), null);
  }

  return (
    <div className="chat-form">
      {question.options.length > 0 ? (
        <div className="chat-row" role="group" aria-label="Answer">
          {question.options.map((option, index) => (
            <button
              key={index}
              type="button"
              className="button"
              disabled={busy}
              onClick={() => void answer(option, index)}
            >
              {option}
            </button>
          ))}
        </div>
      ) : (
        <form className="chat-row" onSubmit={submit}>
          <label className="chat-grow">
            <span className="sr-only">Your answer</span>
            <input
              type="text"
              placeholder="Your answer"
              maxLength={MAX_ANSWER}
              value={text}
              disabled={busy}
              onChange={(event) => setText(event.target.value)}
            />
          </label>
          <button type="submit" className="button" disabled={busy || text.trim() === ''}>
            Answer
          </button>
        </form>
      )}
      {error ? <ErrorNotice error={error} /> : null}
    </div>
  );
}
