/**
 * One Fleet row: a Cell's cells in the table, which at phone width read as one stacked card.
 *
 * The tier badge and the mask badge are in every row, always drawn: `OFF` is drawn too, because
 * no mask is information. The Cell's id and name are the link to its page, with its kind, source
 * and lease age on a muted line under them, so the table has no column for those three. Each data
 * cell carries its column's words in `data-label`, which the stylesheet shows beside the value once
 * the table has become cards, and puts the two badges first. The links are plain hash links: the
 * Warden's Attendant view, then the thoughts of the Warden and of each sub-bee, the sub-bees as
 * numbered comb cells so a row stays two short lines. The highest Cell Wax severity sits under the
 * Alarm count for the same reason. A card (`cards`) spells out what the table's second lines leave
 * to their headers: the bee's id beside its cell, the words "Cell Wax" before the severity.
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
import { BeeLink, ModelsBadge, WardenBadge, WaxMark } from './cell_marks';
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

// The Cell's origin on one muted line: its kind, its source and, when it holds one, its lease's age.
function Origin(props: { row: FleetRow }): ReactElement {
  const { row } = props;
  const lease = leaseAge(row.lease_age_s);
  const parts = [row.kind === 'REAL' ? 'Real' : 'Virtual', row.source];
  if (lease !== 'none') parts.push(`lease ${lease}`);
  return <span className="fleet-origin">{parts.join(' · ')}</span>;
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

// The Attendant view, then the thoughts of the first principal (the Warden) by name and of each
// further bee as a numbered comb cell, each link naming its bee for assistive technology.
function Links(props: { row: FleetRow; cards: boolean }): ReactElement {
  const { warden_id: warden, links } = props.row;
  const [first, ...rest] = links.thought_principals;
  return (
    <td data-label="Links" className="fleet-links">
      {warden ? <a href={href('attendant', warden)}>Attendant</a> : null}
      {first ? (
        <span className="fleet-thoughts">
          <a href={href('thoughts', first)} aria-label={`Thoughts of ${first}`} title={first}>
            Thoughts
          </a>
          {rest.map((bee, index) => (
            <BeeLink
              key={bee}
              bee={bee}
              index={index + 1}
              href={href('thoughts', bee)}
              withId={props.cards}
            />
          ))}
        </span>
      ) : null}
    </td>
  );
}

export function FleetRowView(props: { row: FleetRow; cards: boolean }): ReactElement {
  const { row, cards } = props;
  return (
    <tr>
      <th scope="row" data-label={LABEL.id} className="fleet-id">
        <a href={href('cells', row.id)} className="fleet-page">
          <IdToken id={row.id} /> <span className="fleet-name">{row.name}</span>
        </a>
        <Origin row={row} />
      </th>
      <Cell column="status" className="fleet-status">
        <span>{words(row.status)}</span>
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
      <Cell column="alarms">
        <span className={row.open_alarms > 0 ? 'fleet-alarms' : undefined}>{row.open_alarms}</span>
        <span className="fleet-wax">
          {cards ? <span className="fleet-label">Cell Wax: </span> : null}
          <WaxMark severity={row.highest_wax_severity} />
        </span>
      </Cell>
      <Links row={row} cards={cards} />
    </tr>
  );
}
