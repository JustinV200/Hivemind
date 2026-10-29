/**
 * `LineRow`: one line of the conversation, with its time, kind, links and any form it needs.
 *
 * The human's lines sit on the right and the Queen's on the left (design.md section 5.6). Each
 * shows who wrote it, what kind it is as a word, when, and the words. A question line carries its
 * answer form while the inbox still holds the question, an Alarm line its Acknowledge button; once
 * the inbox no longer holds them the line says what this device answered or acknowledged, or
 * only that it is no longer waiting when it was settled elsewhere. The links come from
 * `line_links.tsx`; a reply's links carry the message it answers as their `title`.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `conversation.tsx`.
 *
 * Key invariants:
 *   - It draws only what the line and the inbox carry; "no longer waiting" is claimed only after
 *     the inbox has been read.
 *   - The kind is always shown as a word.
 *
 * See Also:
 *   - src/views/chat/slice/select.ts for how a line is joined to its inbox item.
 */
import type { ReactElement } from 'react';
import { TimeStamp } from '../../../components';
import { alarmFor, originOf, questionFor, resolvedWords } from '../slice';
import type { ChatLine, ChatState } from '../slice';
import type { ChatActions } from '../writes/use_actions';
import { AlarmForm } from './alarm_form';
import { LineLinks } from './line_links';
import { QuestionForm } from './question_form';

const KIND_WORDS: Record<ChatLine['kind'], string> = {
  message: 'Message',
  reply: 'Reply',
  question: 'Question',
  alarm: 'Alarm',
  notice: 'Notice',
};

// A question and an Alarm wait on the human, so their kind is a status tag in its tone; the
// other kinds take the quiet tag (the shared `.tag` of components.css).
const TAG_CLASS: Partial<Record<ChatLine['kind'], string>> = {
  question: 'tag tag--status tag--warn',
  alarm: 'tag tag--status tag--alarm',
};

export function LineRow(props: {
  line: ChatLine;
  state: ChatState;
  actions: ChatActions;
}): ReactElement {
  const { line, state, actions } = props;
  const question = questionFor(state, line);
  const alarm = alarmFor(state, line);
  const human = line.author === 'human';
  const resolved = resolvedWords(state, line);
  return (
    <li className={`chat-line chat-line--${line.author}`}>
      <article aria-label={`${human ? 'You' : 'Queen'}, ${KIND_WORDS[line.kind]}`}>
        <header className="chat-line-head">
          <strong>{human ? 'You' : 'Queen'}</strong>
          <span className={TAG_CLASS[line.kind] ?? 'tag'}>{KIND_WORDS[line.kind]}</span>
          <TimeStamp at={line.at} />
        </header>
        <p className="chat-text">{line.text}</p>
        <LineLinks
          taskId={line.task_id}
          fromHuman={human && line.kind === 'message'}
          about={originOf(state, line)}
        />
        {question ? <QuestionForm question={question} actions={actions} /> : null}
        {alarm ? <AlarmForm alarm={alarm} actions={actions} /> : null}
        {resolved ? <p className="chat-resolved">{resolved}</p> : null}
      </article>
    </li>
  );
}
