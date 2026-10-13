/**
 * The Fleet table: sortable headers over one row per Cell.
 *
 * A header is a button inside a column header cell; Enter and Space activate it like a click. The
 * sorted column shows an arrow and carries `aria-sort`, the others say `none`. From 1080 px it is
 * a table; below that the stylesheet turns it into stacked cards (the headers become a row of
 * sort buttons above them), so the page never scrolls sideways.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/table/`. Used by `fleet.tsx`, which owns the sort
 *   choice and the order of the rows.
 *
 * Key invariants:
 *   - Rows are keyed by Cell id and update in place.
 *   - Holds no state: the sort comes in as a prop and a click goes out as a callback.
 *   - The table has a caption (visible to assistive technology) and column headers with scope.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 7.
 */
import type { ReactElement } from 'react';
import type { FleetRow } from '../slice';
import { FleetRowView } from './fleet_row';
import { COLUMNS } from './sorting';
import type { SortKey, SortState } from './sorting';

function Header(props: {
  column: (typeof COLUMNS)[number];
  sort: SortState | null;
  onSort: (key: SortKey) => void;
}): ReactElement {
  const { column, sort } = props;
  const active = sort?.key === column.key ? sort : null;
  return (
    <th scope="col" aria-sort={active ? active.direction : 'none'}>
      <button type="button" className="fleet-sort" onClick={() => props.onSort(column.key)}>
        {column.label}
        <span aria-hidden="true">
          {active ? (active.direction === 'ascending' ? ' ▲' : ' ▼') : ''}
        </span>
      </button>
    </th>
  );
}

export function FleetTable(props: {
  rows: readonly FleetRow[];
  sort: SortState | null;
  onSort: (key: SortKey) => void;
}): ReactElement {
  return (
    <div className="fleet-scroll">
      <table className="fleet-table">
        <caption className="sr-only">Cells in the Hive</caption>
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <Header key={column.key} column={column} sort={props.sort} onSort={props.onSort} />
            ))}
            <th scope="col">Links</th>
          </tr>
        </thead>
        <tbody>
          {props.rows.map((row) => (
            <FleetRowView key={row.id} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
