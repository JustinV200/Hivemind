/**
 * `Conversation`: the chat in order, oldest first, then what is still on its way.
 *
 * It draws the slice's lines, then the messages the human sent that have not come back, then the
 * goals they asked for, then the waiting items no line points at. Before the first read it says it is reading; an empty
 * conversation says what would fill it (design.md section 8). The list scrolls with the page while
 * the composer stays docked below it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/chat/conversation/`. Drawn by `chat_screen.tsx`.
 *
 * Key invariants:
 *   - It reads the slice and makes no request; every control is in a child that calls `actions`.
 *   - Lines are keyed by id, so a line settling in place of a pending message does not remount
 *     its neighbours.
 *
 * See Also:
 *   - docs/observation/design.md section 5.6.
 */
import type { ReactElement } from 'react';
import type { ChatState } from '../slice';
import type { ChatActions } from '../writes/use_actions';
import { GoalRow } from './goal_row';
import { LineRow } from './line_row';
import { PendingRow } from './pending_row';
import { Waiting } from './waiting';

export function Conversation(props: {
  state: ChatState;
  actions: ChatActions;
  onDismiss: (key: string) => void;
}): ReactElement {
  const { state, actions } = props;
  const empty = state.lines.length === 0 && state.pending.length === 0 && state.asked.length === 0;
  return (
    <div className="chat-conversation">
      {!state.seeded ? <p aria-busy="true">Reading the chat</p> : null}
      {state.seeded && empty && !state.error ? (
        <p>
          No messages yet. Ask the Queen for something below, and her replies, questions and Alarms
          appear here.
        </p>
      ) : null}
      <ol className="chat-list" aria-label="Conversation">
        {state.lines.map((line) => (
          <LineRow key={line.id} line={line} state={state} actions={actions} />
        ))}
        {state.pending.map((entry) => (
          <PendingRow key={entry.key} entry={entry} onDismiss={props.onDismiss} />
        ))}
        {state.asked.map((asked) => (
          <GoalRow key={asked.id} asked={asked} state={state} actions={actions} />
        ))}
      </ol>
      <Waiting state={state} actions={actions} />
    </div>
  );
}
