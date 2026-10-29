/**
 * `InboxPanel`: the Attendant's current ordering of the Queen's inbox, with each score and why.
 *
 * Drawn for the Queen only. It reads `GET /v1/attendant/queen` once when it mounts and lists the
 * inbox highest score first, as the Entrance ordered it. Each entry shows its kind, its score, the
 * scorer's reasons, the task it concerns and when it began waiting. When the Entrance says the
 * inbox was rebuilt from durable state (the supervisor's live queue cannot be read), a small note
 * says so in plain words: an estimated order, scored from saved state.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/thoughts/`. Drawn by `thoughts_view.tsx` when the bee
 *   is `queen`.
 *
 * Key invariants:
 *   - One read, no polling; a refusal or failure is shown through `ErrorNotice`.
 *   - The order is the Entrance's; the panel never re-sorts and shows only fields the view has.
 *   - Read-only: nothing here answers or acknowledges an entry.
 *
 * See Also:
 *   - docs/observation/design.md sections 5.4 and 5.5.
 */
import { useEffect, useState } from 'react';
import type { ReactElement } from 'react';
import { EmptyState, ErrorNotice, IdToken, inboxKindWords, TimeStamp } from '../../components';
import type { components } from '../../landing_board/types';
import { useHive } from '../../store';

type Attendant = components['schemas']['AttendantView'];
type Entry = components['schemas']['InboxEntryView'];

type Load =
  | { status: 'loading' }
  | { status: 'failed'; error: unknown }
  | { status: 'ready'; view: Attendant };

function Row(props: { entry: Entry; rank: number }): ReactElement {
  const { entry, rank } = props;
  return (
    <li className="inbox-entry">
      <span className="inbox-rank">{rank}</span>
      <span className="inbox-kind">{inboxKindWords(entry.kind)}</span>
      {entry.severity ? <span className="tag tag--status tag--alarm">{entry.severity}</span> : null}
      <IdToken id={entry.id} />
      <span className="inbox-score" title="higher is handled first">
        score <strong>{entry.score}</strong>
      </span>
      <TimeStamp at={entry.received_at} />
      {entry.task_id ? (
        <span>
          task <IdToken id={entry.task_id} />
        </span>
      ) : null}
      {entry.reasons.length > 0 ? (
        <ul className="inbox-reasons">
          {entry.reasons.map((reason, i) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function InboxPanel(): ReactElement {
  const { client } = useHive();
  const [load, setLoad] = useState<Load>({ status: 'loading' });

  useEffect(() => {
    let current = true;
    client.http
      .call('get', '/v1/attendant/{principal}', { params: { principal: 'queen' } })
      .then((view) => current && setLoad({ status: 'ready', view }))
      .catch((error: unknown) => current && setLoad({ status: 'failed', error }));
    return () => {
      current = false;
    };
  }, [client]);

  if (load.status === 'failed') return <ErrorNotice error={load.error} />;
  if (load.status === 'loading') return <p aria-busy="true">Reading the Queen&apos;s inbox</p>;
  const { inbox, inbox_is_reconstructed } = load.view;
  return (
    <section className="inbox-panel" aria-labelledby="inbox-title">
      <h2 id="inbox-title" className="section-heading">
        Queen&apos;s inbox, in the Attendant&apos;s order
      </h2>
      {inbox_is_reconstructed ? (
        <p className="inbox-note">
          Estimated order, scored from saved state, not the Queen&apos;s live queue.
        </p>
      ) : null}
      {inbox.length === 0 ? (
        <EmptyState>Nothing is waiting in the Queen&apos;s inbox.</EmptyState>
      ) : (
        <ol className="inbox-list">
          {inbox.map((entry, i) => (
            <Row key={entry.id} entry={entry} rank={i + 1} />
          ))}
        </ol>
      )}
    </section>
  );
}
