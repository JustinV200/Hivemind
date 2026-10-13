/**
 * `WardensTable`: each Warden attached to the Queen, with the grants it holds and its Heartbeat.
 *
 * It answers "who holds the Forage I see flowing": the Warden's state (as its newest Heartbeat said,
 * or offline once the Queen marked it so), how many sub-bees it listed, how many live grants it
 * holds in the ledger and when it last reported. Under 720 px each Warden is a stacked card.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/tables/`. Drawn by `forage_screen.tsx` from the
 *   slice's `wardens`.
 *
 * Key invariants:
 *   - Read-only and stateless; Wardens keep the Queen's attachment order.
 *   - The state is always a word; its dot is decoration.
 *
 * See Also:
 *   - docs/observation/design.md section 4.4 (status words).
 */
import type { ReactElement } from 'react';
import { IdToken, StatusDot, TimeStamp } from '../../../components';
import type { WardenView } from '../slice';

// An offline Warden reads `offline`; a live one shows its state, as a status word where one exists.
function State(props: { warden: WardenView }): ReactElement {
  const { warden } = props;
  if (warden.offline) return <StatusDot word="offline" />;
  if (warden.state === 'ACTIVE') return <StatusDot word="active" />;
  if (warden.state === 'WATCH') return <StatusDot word="watch" />;
  return <span>{warden.state ?? 'no Heartbeat yet'}</span>;
}

export function WardensTable(props: { wardens: readonly WardenView[] }): ReactElement {
  return (
    <table className="forage-table">
      <caption>Wardens and the grants they hold</caption>
      <thead>
        <tr>
          {['Warden', 'Cell', 'State', 'Sub-bees', 'Live grants', 'Last Heartbeat', 'Missed'].map(
            (label) => (
              <th key={label} scope="col">
                {label}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {props.wardens.map((warden) => (
          <tr key={warden.id}>
            <td data-label="Warden">
              <IdToken id={warden.id} />
            </td>
            <td data-label="Cell">
              <IdToken id={warden.cell_id} />
            </td>
            <td data-label="State">
              <State warden={warden} />
            </td>
            <td data-label="Sub-bees">{warden.sub_bees}</td>
            <td data-label="Live grants">{warden.live_grants}</td>
            <td data-label="Last Heartbeat">
              {warden.last_heartbeat_at ? <TimeStamp at={warden.last_heartbeat_at} /> : '-'}
            </td>
            <td data-label="Missed">{warden.missed_heartbeats}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
