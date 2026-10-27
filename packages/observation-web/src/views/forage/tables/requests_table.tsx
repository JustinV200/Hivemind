/**
 * `RequestsTable`: the Forage requests seen on the stream, with the Queen's answer when it lands.
 *
 * A Warden that needs shared Forage beyond its standing grant sends a request; the Queen grants it
 * (in full or in part) or denies it with a reason code. A request with no answer yet is "pending".
 * The list is built from the trail events the stream carries, so it holds the requests since this
 * view opened (the read routes do not list open requests). Under 1080 px each request is a card.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/tables/`. Drawn by `forage_screen.tsx` from the
 *   slice's `requests`.
 *
 * Key invariants:
 *   - Read-only and stateless; newest request first, as the slice keeps them.
 *   - The decision is always a word (pending, granted, denied); its dot is decoration.
 *
 * See Also:
 *   - src/views/forage/slice/requests.ts for how a request and its answer are paired.
 */
import type { ReactElement } from 'react';
import { IdToken, TimeStamp } from '../../../components';
import type { Decision, RequestRecord } from '../slice';

const TONE: Readonly<Record<Decision, string>> = {
  pending: 'warn',
  granted: 'ok',
  denied: 'alarm',
};

function Row(props: { request: RequestRecord }): ReactElement {
  const { request } = props;
  return (
    <tr>
      <td data-label="Asked">
        <TimeStamp at={request.requestedAt} />
      </td>
      <td data-label="Grant">
        <IdToken id={request.grantId} />
      </td>
      <td data-label="Asked for">{request.requestKind ?? '-'}</td>
      <td data-label="Queen's decision">
        <span className="status">
          <span className={`status-dot status-dot--${TONE[request.decision]}`} aria-hidden="true" />
          <span>{request.decision}</span>
        </span>
      </td>
      <td data-label="Answered">
        {request.decidedAt ? <TimeStamp at={request.decidedAt} /> : '-'}
      </td>
      <td data-label="Reason">{request.reason ? <code>{request.reason}</code> : '-'}</td>
    </tr>
  );
}

export function RequestsTable(props: { requests: readonly RequestRecord[] }): ReactElement {
  return (
    <table className="forage-table">
      <caption>Forage requests and the Queen&apos;s decisions</caption>
      <thead>
        <tr>
          {['Asked', 'Grant', 'Asked for', "Queen's decision", 'Answered', 'Reason'].map(
            (label) => (
              <th key={label} scope="col">
                {label}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {props.requests.map((request) => (
          <Row key={request.id} request={request} />
        ))}
      </tbody>
    </table>
  );
}
