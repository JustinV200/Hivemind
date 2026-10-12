/**
 * One Fleet row: a Cell's cells in the table, which at phone width read as one stacked card.
 *
 * The tier badge and the mask badge are in every row, always drawn: `OFF` is drawn too, because
 * no mask is information. Each data cell carries its column's words in `data-label`, which the
 * stylesheet shows beside the value once the table has become cards, and puts the two badges first.
 * The links are plain hash links: the Cell's page, its Warden's Attendant view and the thoughts of
 * its Warden and sub-bees.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/table/`. Used by `fleet_table.tsx`.
 *
 * Key invariants:
 *   - Nothing is hidden by width: the same cells are in the page at every size.
 *   - Everything drawn comes from the row; a missing value is the word `none`.
 *   - Ids in a hash are percent-encoded; the router decodes them once.
 *
 * See Also:
 *   - docs/observation/design.md sections 2, 4.1 and 5.1; codingrules 8.11.
 */
import type { ReactElement, ReactNode } from 'react';
import { IdToken, MaskBadge, StatusDot, TierBadge } from '../../../components';
import type { FleetRow } from '../slice';
import { ModelsBadge, WardenBadge, WaxMark } from './cell_marks';
import { leaseAge, words } from './format';
import { COLUMNS } from './sorting';
import type { SortKey } from './sorting';

const LABEL = Object.fromEntries(COLUMNS.map((c) => [c.key, c.label])) as Record<SortKey, string>;
const href = (route: string, id: string): string => `#/${route}/${encodeURIComponent(id)}`;

function Cell(props: { column: SortKey; className?: string; children: ReactNode }): ReactElement {
  return (
    <td data-label={LABEL[props.column]} className={props.className}>
      {props.children}
    </td>
  );
}

function Task(props: { row: FleetRow }): ReactElement {
  const { current_task_id: task, current_task_role: role } = props.row;
  if (task === null) return <span className="fleet-none">none</span>;
  return (
    <>
      <IdToken id={task} />
      {role ? <span className="fleet-role">{words(role)}</span> : null}
    </>
  );
}

function Links(props: { row: FleetRow }): ReactElement {
  const { id, warden_id: warden, links } = props.row;
  return (
    <td data-label="Links" className="fleet-links">
      <a href={href('cells', id)}>Page</a>
      {warden ? <a href={href('attendant', warden)}>Attendant</a> : null}
      {links.thought_principals.length > 0 ? (
        <span className="fleet-thoughts">
          <span>Thoughts</span>
          {links.thought_principals.map((bee) => (
            <a key={bee} href={href('thoughts', bee)} aria-label={`Thoughts of ${bee}`}>
              <IdToken id={bee} />
            </a>
          ))}
        </span>
      ) : null}
    </td>
  );
}

export function FleetRowView(props: { row: FleetRow }): ReactElement {
  const { row } = props;
  return (
    <tr>
      <th scope="row" data-label={LABEL.id} className="fleet-id">
        <a href={href('cells', row.id)}>
          <IdToken id={row.id} />
        </a>
        <span className="fleet-name">{row.name}</span>
      </th>
      <Cell column="kind">{row.kind === 'REAL' ? 'Real' : 'Virtual'}</Cell>
      <Cell column="source">{row.source}</Cell>
      <Cell column="status">
        <span className="fleet-status">{row.status}</span>
        {row.mode ? <StatusDot word={row.mode === 'ACTIVE' ? 'active' : 'watch'} /> : null}
      </Cell>
      <Cell column="tier" className="fleet-tier">
        <TierBadge level={row.comb_shield} />
      </Cell>
      <Cell column="mask" className="fleet-mask">
        <MaskBadge state={row.mask_state ?? 'OFF'} />
      </Cell>
      <Cell column="access">{words(row.access_level)}</Cell>
      <Cell column="warden">
        <WardenBadge runs={row.warden_runs} />
      </Cell>
      <Cell column="models">
        <ModelsBadge from={row.models_from} />
      </Cell>
      <Cell column="task">
        <Task row={row} />
      </Cell>
      <Cell column="alarms" className={row.open_alarms > 0 ? 'fleet-alarms' : undefined}>
        {row.open_alarms}
      </Cell>
      <Cell column="wax">
        <WaxMark severity={row.highest_wax_severity} />
      </Cell>
      <Cell column="lease">{leaseAge(row.lease_age_s)}</Cell>
      <Links row={row} />
    </tr>
  );
}
