/**
 * `Waiting`: questions and Alarms the inbox holds that no chat line points at.
 *
 * Normally a waiting question or Alarm is drawn under its own line. If the inbox holds one whose
 * line is not in the conversation (the line fell outside the page read, or the stream had not
 * delivered it yet), it is drawn here instead, with the same form, so a question the human can
 * answer is never hidden.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `conversation.tsx` below
 *   the lines, from `unmatchedInbox` in the slice's selectors.
 *
 * Key invariants:
 *   - Renders nothing when every waiting item has a line.
 *   - Each item links to its task when the schema names one.
 *
 * See Also:
 *   - src/views/chat/slice/select.ts for `unmatchedInbox`.
 */
import type { ReactElement } from 'react';
import { TimeStamp } from '../../../components';
import { unmatchedInbox } from '../slice';
import type { ChatState } from '../slice';
import type { ChatActions } from '../writes/use_actions';
import { AlarmForm } from './alarm_form';
import { LineLinks } from './line_links';
import { QuestionForm } from './question_form';

export function Waiting(props: { state: ChatState; actions: ChatActions }): ReactElement | null {
  const { questions, alarms } = unmatchedInbox(props.state);
  if (questions.length === 0 && alarms.length === 0) return null;
  return (
    <section className="chat-waiting" aria-label="Waiting for you">
      <h2 className="section-heading">Waiting for you</h2>
      <ul className="chat-list">
        {questions.map((question) => (
          <li key={question.id} className="chat-line chat-line--queen">
            <article aria-label="Queen, Question">
              <header className="chat-line-head">
                <strong>Queen</strong>
                <span className="chat-tag chat-tag--question">Question</span>
                <TimeStamp at={question.asked_at} />
              </header>
              <p className="chat-text">{question.text}</p>
              <LineLinks taskId={question.task_id} fromHuman={false} />
              <QuestionForm question={question} actions={props.actions} />
            </article>
          </li>
        ))}
        {alarms.map((alarm) => (
          <li key={alarm.id} className="chat-line chat-line--queen">
            <article aria-label="Queen, Alarm">
              <header className="chat-line-head">
                <strong>Queen</strong>
                <span className="chat-tag chat-tag--alarm">Alarm</span>
                <TimeStamp at={alarm.raised_at} />
              </header>
              <p className="chat-text">{alarm.detail}</p>
              <LineLinks taskId={alarm.task_id} fromHuman={false} />
              <AlarmForm alarm={alarm} actions={props.actions} />
            </article>
          </li>
        ))}
      </ul>
    </section>
  );
}
