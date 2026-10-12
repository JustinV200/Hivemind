/**
 * `GrantsTable`: every live grant, those nearing expiry first and marked.
 *
 * A grant is the Queen's lease of Forage to a Warden: seats, sub-bee slots, tokens and spend, until
 * `expires_at` unless the Warden's Heartbeat renews it. The table lists the grants that end within
 * ten minutes first (soonest first), each with the word "expiring soon" and the warn token, then the
 * rest in the ledger's order. Spend is a gauge of what the grant has spent against what it allows.
 * Under 720 px each grant becomes a stacked card.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/tables/`. Drawn by `forage_screen.tsx` from the
 *   slice's grants and the screen's clock.
 *
 * Key invariants:
 *   - Read-only and stateless: it draws its props.
 *   - Rows are keyed by grant id, so a revision updates a row in place.
 *   - The warn colour always comes with the words "expiring soon".
 *
 * See Also:
 *   - src/views/forage/tables/expiry.ts for the rule and the order.
 */
import type { ReactElement } from 'react';
import { Gauge, IdToken, TimeStamp } from '../../../components';
import { count, leaseText } from '../format';
import type { GrantView } from '../slice';
import { isNearExpiry, orderGrants } from './expiry';

function Row(props: { grant: GrantView; nowMs: number }): ReactElement {
  const { grant, nowMs } = props;
  const near = isNearExpiry(grant.expires_at, nowMs);
  return (
    <tr className={near ? 'forage-near' : undefined}>
      <td data-label="Grant">
        <IdToken id={grant.id} />
      </td>
      <td data-label="Warden">
        <IdToken id={grant.holder} />
      </td>
      <td data-label="Cell">
        <IdToken id={grant.cell_id} />
      </td>
      <td data-label="Task">{grant.task_id ? <IdToken id={grant.task_id} /> : '-'}</td>
      <td data-label="State">{grant.state}</td>
      <td data-label="Seats">{count(grant.seats)}</td>
      <td data-label="Sub-bees">{count(grant.max_sub_bees)}</td>
      <td data-label="Tokens">{`${count(grant.tokens_spent)} / ${count(grant.token_budget)}`}</td>
      <td data-label="Spend (USD)">
        <Gauge used={grant.spent} budget={grant.spend_budget} label={`Spend of ${grant.id}`} />
      </td>
      <td data-label="Expires">
        <TimeStamp at={grant.expires_at} /> <span>{leaseText(grant.expires_at, nowMs)}</span>
        {near ? <strong className="forage-near-word"> expiring soon</strong> : null}
      </td>
    </tr>
  );
}

export function GrantsTable(props: { grants: readonly GrantView[]; nowMs: number }): ReactElement {
  const rows = orderGrants(props.grants, props.nowMs);
  return (
    <table className="forage-table">
      <caption>Grants: the Forage each Warden holds, soonest to lapse first</caption>
      <thead>
        <tr>
          {[
            'Grant',
            'Warden',
            'Cell',
            'Task',
            'State',
            'Seats',
            'Sub-bees',
            'Tokens',
            'Spend (USD)',
            'Expires',
          ].map((label) => (
            <th key={label} scope="col">
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((grant) => (
          <Row key={grant.id} grant={grant} nowMs={props.nowMs} />
        ))}
      </tbody>
    </table>
  );
}
