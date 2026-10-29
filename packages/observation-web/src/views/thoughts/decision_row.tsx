/**
 * `DecisionRow`: one deterministic decision of the Queen's autopilot, drawn apart from episodes.
 *
 * Autopilot handles the routine without waking a model, and the record of each decision is a
 * trail event: what kind, what it did, the reason code recorded with it, what it concerned and the
 * Alarm that prompted it. It is drawn as a slim row with a left rule and the label "Autopilot
 * decision", so it reads as different from an episode's card at a glance, in the same timeline.
 * The codes are drawn as words (design.md section 8): the kind as what the Queen did, the action
 * in plain case, the reason through the Alarm kinds' table (`a Worker crashed`), each with the
 * code on its `title`, so the row reads beside the episodes' glossed triggers rather than
 * shouting past them.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Drawn by `thoughts_view.tsx` for each
 *   decision in the timeline (the Queen's page only; other bees have none).
 *
 * Key invariants:
 *   - Read-only and stateless.
 *   - Only fields the decision carries are shown; a null action, reason or Alarm is left out.
 *
 * See Also:
 *   - docs/observation/design.md section 5.4.
 */
import type { ReactElement } from 'react';
import { alarmKindWords, IdToken, plainCode, TimeStamp } from '../../components';
import type { DecisionView } from './slice';

// The two trail kinds a decision is read from (`DECISION_KINDS`), as what the Queen did.
const KIND_WORDS: Readonly<Record<string, string>> = {
  'queen.decided': 'the Queen decided',
  'alarm.handled': 'the Queen handled an Alarm',
};

export function DecisionRow(props: { decision: DecisionView }): ReactElement {
  const d = props.decision;
  return (
    <article className="thought-decision" aria-label="Autopilot decision">
      <TimeStamp at={d.at} />
      <span className="tag">Autopilot decision</span>
      <span title={d.kind}>{KIND_WORDS[d.kind] ?? plainCode(d.kind)}</span>
      {d.action ? <strong title={d.action}>{plainCode(d.action)}</strong> : null}
      {d.reason ? (
        // A reason is often an Alarm kind in lower case (`worker_crashed`); the table reads it
        // either way, and a code it does not know is shown in plain case.
        <span title={d.reason}>
          <span className="thought-label">reason</span> {alarmKindWords(d.reason.toUpperCase())}
        </span>
      ) : null}
      <span>
        on <IdToken id={d.subject_id} />
      </span>
      {d.alarm_id ? (
        <span>
          Alarm <IdToken id={d.alarm_id} />
        </span>
      ) : null}
      <IdToken id={d.id} />
    </article>
  );
}
