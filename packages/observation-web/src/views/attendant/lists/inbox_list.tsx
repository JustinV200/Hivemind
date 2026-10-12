/**
 * `InboxList`: the principal's inbox in the Attendant's order, each entry with its score and why.
 *
 * The Entrance orders the inbox (highest score first) and this list never re-sorts it: the rank is
 * the position. Each entry shows its rank, score, kind, an Alarm's severity, its id (the subject
 * the schema carries: an Alarm, a question or a chat entry), the task it concerns, when it began
 * waiting and the scorer's reasons. When the Entrance says the inbox was rebuilt from durable
 * state, a note says so in the schema's own terms.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/attendant/lists/`. Drawn by `attendant.tsx` beside the
 *   task graph.
 *
 * Key invariants:
 *   - Read-only: nothing here answers or acknowledges an entry.
 *   - Only fields of `InboxEntryView` are shown; there is no subject text in the schema, so the
 *     id and the task id stand for it.
 *
 * See Also:
 *   - docs/observation/design.md section 5.5.
 */
import type { ReactElement } from 'react';
import { IdToken, TimeStamp } from '../../../components';
import type { components } from '../../../landing_board/types';

type Entry = components['schemas']['InboxEntryView'];

const kindWord = (kind: Entry['kind']): string => kind.toLowerCase().replace(/_/g, ' ');

function Row(props: { entry: Entry; rank: number }): ReactElement {
  const { entry, rank } = props;
  return (
    <li className="att-entry">
      <span className="att-rank">{rank}</span>
      <div className="att-entry-body">
        <div className="att-row">
          <span className="att-kind">{kindWord(entry.kind)}</span>
          {entry.severity ? <span className="att-tag">{entry.severity}</span> : null}
          <IdToken id={entry.id} />
          <span className="att-score">
            score <strong>{Number(entry.score.toFixed(2))}</strong>
          </span>
        </div>
        <div className="att-row att-muted">
          {entry.task_id ? (
            <span>
              task <IdToken id={entry.task_id} />
            </span>
          ) : null}
          <span>
            waiting since <TimeStamp at={entry.received_at} />
          </span>
        </div>
        {entry.reasons.length > 0 ? (
          <ul className="att-reasons">
            {entry.reasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </li>
  );
}

export function InboxList(props: {
  inbox: readonly Entry[];
  reconstructed: boolean;
}): ReactElement {
  return (
    <section className="att-panel" aria-labelledby="att-inbox-title">
      <h2 id="att-inbox-title">Inbox, in the Attendant&apos;s order</h2>
      {props.reconstructed ? (
        <p className="att-note">
          Rebuilt from durable state and scored with the Attendant&apos;s own scorer; the
          supervisor&apos;s live queue cannot be read.
        </p>
      ) : null}
      {props.inbox.length === 0 ? (
        <p className="att-muted">Nothing is waiting in this inbox.</p>
      ) : (
        <ol className="att-list">
          {props.inbox.map((entry, i) => (
            <Row key={entry.id} entry={entry} rank={i + 1} />
          ))}
        </ol>
      )}
    </section>
  );
}
