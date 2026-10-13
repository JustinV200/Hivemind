/**
 * The Chat view: the conversation with the Queen, the one write path of the Observation Hive.
 *
 * At `#/chat` the human reads the Queen's side (her replies, questions and Alarms) and their own
 * messages in order, answers a question or acknowledges an Alarm where it is asked, and writes or
 * speaks to her from a composer docked to the bottom. The page seeds from `GET /v1/chat` and
 * `GET /v1/inbox` once, and the chat stream keeps it live from the last line's position. A device
 * without `entrance:submit` is told so and gets no composer. Each line links to the tasks it
 * concerns; there is no break-glass control here, because none is built in this step.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/`. Chosen by the outlet in `src/shell/` when the
 *   route is `chat`. Reads and writes only the Landing Board through the store's client.
 *
 * Key invariants:
 *   - The only requests it makes are `GET /v1/chat`, `GET /v1/inbox`, and the writes in
 *     `writes/use_actions.ts`; a test lists them.
 *   - A 403 on the chat read hides the composer and shows the refusal's sentence.
 *   - A newly arrived question or Alarm is announced once, politely, to assistive technology.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6 and src/views/chat/README.md.
 */
import type { ReactElement } from 'react';
import { ErrorNotice, ViewTitle } from '../../components';
import { LandingBoardError } from '../../landing_board/client';
import { useHive, useSlice, useViewStream } from '../../store';
import './chat.css';
import { Dock } from './composer/dock';
import { Conversation } from './conversation/conversation';
import { CHAT_RESUME, CHAT_STREAM, toChatEvent } from './reads/chat_stream';
import { useChat } from './reads/use_chat';
import { useChatActions } from './writes/use_actions';

// One sentence about something that changed under the human, with a way to clear it.
function Notice(props: { text: string; onDismiss: () => void }): ReactElement {
  return (
    <p className="chat-note" role="status">
      {props.text}{' '}
      <button type="button" className="button" onClick={props.onDismiss}>
        Dismiss
      </button>
    </p>
  );
}

export function ChatScreen(): ReactElement {
  const { store } = useHive();
  const state = useSlice('chat');
  useChat();
  useViewStream(CHAT_STREAM, 'chat', toChatEvent, CHAT_RESUME);
  const { actions, stepUp } = useChatActions();
  const dispatch = store.dispatch;

  // No `entrance:submit` (or no clearance): nothing can be sent, so no composer is offered.
  const refused = state.error instanceof LandingBoardError && state.error.status === 403;
  return (
    <section className="chat">
      <ViewTitle title="Chat" subject="Queen" />
      <p className="sr-only" role="status" aria-live="polite">
        {state.announce ? `The Queen wrote: ${state.announce.text}` : ''}
      </p>
      {state.error ? <ErrorNotice error={state.error} /> : null}
      {state.inboxError && !refused ? <ErrorNotice error={state.inboxError} /> : null}
      {state.notice ? (
        <Notice
          text={state.notice}
          onDismiss={() => dispatch({ slice: 'chat', event: { type: 'dismiss_notice' } })}
        />
      ) : null}
      <Conversation
        state={state}
        actions={actions}
        onDismiss={(key) => dispatch({ slice: 'chat', event: { type: 'dismiss_pending', key } })}
      />
      {refused ? null : <Dock state={state} actions={actions} stepUp={stepUp} />}
    </section>
  );
}
