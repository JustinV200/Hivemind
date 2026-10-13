/**
 * `PendingRow`: a message the human sent that has not come back as a line yet.
 *
 * The text shows at once, marked with where it is: sending, or accepted and waiting for the
 * Queen's inbox to log it. It settles (this row disappears and the real line takes its place)
 * when the line with the id the Entrance returned arrives. A refused message stays, with the
 * refusal and a Dismiss button, so the words are not lost.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `conversation.tsx` after
 *   the lines, from the slice's `pending`.
 *
 * Key invariants:
 *   - The state is a word as well as a style.
 *   - Dismissing only removes it from this device's view; it was never accepted by the Entrance.
 *
 * See Also:
 *   - src/views/chat/slice/reduce.ts for how a pending message settles.
 */
import type { ReactElement } from 'react';
import { ErrorNotice, TimeStamp } from '../../../components';
import type { PendingMessage } from '../slice';

const STATUS_WORDS = {
  sending: 'Sending',
  sent: 'Sent, waiting for the Queen to log it',
  failed: 'Not sent',
} as const;

export function PendingRow(props: {
  entry: PendingMessage;
  onDismiss: (key: string) => void;
}): ReactElement {
  const { entry } = props;
  return (
    <li className="chat-line chat-line--human chat-line--pending">
      <article aria-label={`You, ${STATUS_WORDS[entry.status]}`}>
        <header className="chat-line-head">
          <strong>You</strong>
          <span className="chat-tag chat-tag--pending">{STATUS_WORDS[entry.status]}</span>
          <TimeStamp at={entry.at} />
        </header>
        <p className="chat-text">{entry.text}</p>
        {entry.status === 'failed' ? (
          <div className="chat-form">
            <ErrorNotice error={entry.error} />
            <div className="chat-row">
              <button type="button" className="button" onClick={() => props.onDismiss(entry.key)}>
                Dismiss
              </button>
            </div>
          </div>
        ) : null}
      </article>
    </li>
  );
}
