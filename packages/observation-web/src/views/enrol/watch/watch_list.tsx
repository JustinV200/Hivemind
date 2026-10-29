/**
 * `WatchList`: the tasks that wait for the owner's yes to use the running screen or audio.
 *
 * ADR-0069: each card says what is asked for (the operator's own screen, the sound server, or
 * both), on which machine, for which task, and which bee would hold it, so the owner can decide
 * on that one task. Allow lets it start; Refuse cancels it. The Queen refuses it herself when
 * nobody answers by the time shown. Words, not colour, carry every meaning here.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/enrol/watch/`. Drawn by `enrol_screen.tsx` on the Hive
 *   Stand only; the calls are `actions.answerWatch`.
 *
 * Key invariants:
 *   - Nothing a task's planner wrote (its title) is drawn as markup.
 *   - Allow and Refuse are disabled while any answer is in flight.
 *
 * See Also:
 *   - docs/adr/0069-the-owner-approves-the-running-screen-and-audio-at-the-hive-stand.md.
 */
import type { ReactElement } from 'react';
import { EmptyState, TimeStamp } from '../../../components';
import type { EnrolActions } from '../use_enrol_actions';
import type { WatchRequest } from './use_watch';

// What each scope lets the task do, in the owner's words.
const SCOPE_WORDS: Record<string, string> = {
  'exoskeleton:real_display': 'see and use your screen',
  'exoskeleton:audio': 'hear and play sound',
};

const describeScopes = (scopes: readonly string[]): string =>
  scopes.map((scope) => SCOPE_WORDS[scope] ?? scope).join(' and ');

function Card(props: {
  request: WatchRequest;
  actions: EnrolActions;
  onAnswered: () => void;
}): ReactElement {
  const { request, actions } = props;
  const answer = async (approve: boolean): Promise<void> => {
    if (await actions.answerWatch(request.id, approve, request.title)) props.onAnswered();
  };
  return (
    <li
      className="enrol-pending comb-cell comb-cell--strong"
      aria-label={`Waiting: ${request.title}`}
    >
      <p>
        <strong>{request.title}</strong> asks to {describeScopes(request.scopes)}
      </p>
      <dl className="enrol-facts">
        <div>
          <dt>Machine</dt>
          <dd>{request.cell_name}</dd>
        </div>
        <div>
          <dt>Asked by</dt>
          <dd>
            {request.role} under {request.warden_id}
          </dd>
        </div>
        <div>
          <dt>Task</dt>
          <dd>{request.task_id}</dd>
        </div>
        <div>
          <dt>Refused unless answered by</dt>
          <dd>
            <TimeStamp at={request.expires_at} />
          </dd>
        </div>
      </dl>
      <div className="enrol-row">
        <button
          type="button"
          className="button"
          aria-label={`Allow ${request.title}`}
          disabled={actions.busy}
          onClick={() => void answer(true)}
        >
          Allow
        </button>
        <button
          type="button"
          className="button button--danger"
          aria-label={`Refuse ${request.title}`}
          disabled={actions.busy}
          onClick={() => void answer(false)}
        >
          Refuse
        </button>
      </div>
    </li>
  );
}

export function WatchList(props: {
  requests: readonly WatchRequest[];
  actions: EnrolActions;
  onAnswered: () => void;
}): ReactElement {
  if (props.requests.length === 0) {
    return <EmptyState>No task is waiting to use your screen or sound.</EmptyState>;
  }
  return (
    <ul className="enrol-pending-list">
      {props.requests.map((request) => (
        <Card
          key={request.id}
          request={request}
          actions={props.actions}
          onAnswered={props.onAnswered}
        />
      ))}
    </ul>
  );
}
