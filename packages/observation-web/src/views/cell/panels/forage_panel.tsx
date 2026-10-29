/**
 * `ForagePanel`: the Forage the Warden holds, used against issued, and what it is waiting on.
 *
 * The top of the panel is the Warden's whole grant: tokens spent against the tokens issued and
 * dollars spent against the dollars issued, as gauges that say their numbers in text. Under it,
 * each live grant (state, revision, shared seats, sub-bee limit, when it lapses unless a Heartbeat
 * renews it) with the same two gauges, each named by the word beside it as the top rows are, so
 * a sighted reader never tells tokens from spend by position alone; then each request the
 * Warden has filed and not had answered. A Cell with no Warden
 * holds nothing, and the panel says so.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cell/panels/`. Rendered by `cell.tsx` from the page.
 *
 * Key invariants:
 *   - Numbers come from the page as given; a zero budget draws an empty gauge (never NaN).
 *   - An expiry is shown as a time, not as a countdown, so the panel needs no timer.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.2 and 5.2.
 */
import type { ReactElement } from 'react';
import { Gauge, IdToken, plainCode, TimeStamp } from '../../../components';
import type { CellPage } from '../slice';
import { Panel, Tag } from './panel';

type Forage = NonNullable<CellPage['forage']>;
type Grant = Forage['grants'][number];

function GrantRow(props: { grant: Grant }): ReactElement {
  const { grant } = props;
  return (
    <li className="cell-item">
      <div className="cell-row">
        <IdToken id={grant.id} />
        {/* The state as a word (`active`), as the Forage's Grants table writes it */}
        <Tag tone={grant.state === 'EXHAUSTED' ? 'alarm' : 'ok'} title={grant.state}>
          {plainCode(grant.state)}
        </Tag>
        <span>{`revision ${grant.revision}`}</span>
        <span>{`${grant.seats} seats`}</span>
        <span>{`up to ${grant.max_sub_bees} sub-bees`}</span>
        <span>
          lapses <TimeStamp at={grant.expires_at} />
        </span>
      </div>
      <div className="cell-gauges">
        <span>Tokens</span>
        <Gauge
          used={grant.tokens_spent}
          budget={grant.token_budget}
          label={`Tokens of ${grant.id}`}
        />
        <span>Spend (USD)</span>
        <Gauge used={grant.spent} budget={grant.spend_budget} label={`Spend of ${grant.id}, USD`} />
      </div>
    </li>
  );
}

export function ForagePanel(props: { page: CellPage }): ReactElement {
  const forage = props.page.forage;
  if (forage === null) {
    return (
      <Panel id="forage" title="Forage">
        <p>No Warden is attached, so this Cell holds no Forage.</p>
      </Panel>
    );
  }
  return (
    <Panel id="forage" title="Forage">
      <div className="cell-gauges">
        <span>Tokens</span>
        <Gauge used={forage.tokens_spent} budget={forage.token_budget} label="Tokens, all grants" />
        <span>Spend (USD)</span>
        <Gauge
          used={forage.spent_usd}
          budget={forage.spend_budget_usd}
          label="Spend, all grants, USD"
        />
      </div>
      {forage.grants.length === 0 ? <p>No live grants.</p> : null}
      <ul className="cell-list">
        {forage.grants.map((grant) => (
          <GrantRow key={grant.id} grant={grant} />
        ))}
      </ul>
      <h3>Pending requests</h3>
      {forage.pending_requests.length === 0 ? <p>None waiting for an answer.</p> : null}
      <ul className="cell-list">
        {forage.pending_requests.map((request) => (
          <li key={`${request.grant_id}:${request.requested_at}`} className="cell-row">
            {/* The kind is a code (`SHARED_SEATS`), read as words with the code on title */}
            <span title={request.request_kind ?? undefined}>
              {request.request_kind ? plainCode(request.request_kind) : 'unknown kind'}
            </span>
            <span>
              for grant <IdToken id={request.grant_id} />
            </span>
            <TimeStamp at={request.requested_at} />
          </li>
        ))}
      </ul>
    </Panel>
  );
}
