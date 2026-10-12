/**
 * `DecisionRow`: one deterministic decision of the Queen's autopilot, drawn apart from episodes.
 *
 * Autopilot handles the routine without waking a model, and the record of each decision is a
 * trail event: what kind, what it did, the reason code recorded with it, what it concerned and the
 * Alarm that prompted it. It is drawn as a slim row with a left rule and the label "Autopilot
 * decision", so it reads as different from an episode's card at a glance, in the same timeline.
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
import { IdToken, TimeStamp } from '../../components';
import type { DecisionView } from './slice';

export function DecisionRow(props: { decision: DecisionView }): ReactElement {
  const d = props.decision;
  return (
    <article className="thought-decision" aria-label="Autopilot decision">
      <TimeStamp at={d.at} />
      <span className="thought-tag">Autopilot decision</span>
      <code>{d.kind}</code>
      {d.action ? <strong>{d.action}</strong> : null}
      {d.reason ? (
        <span>
          reason <code>{d.reason}</code>
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
