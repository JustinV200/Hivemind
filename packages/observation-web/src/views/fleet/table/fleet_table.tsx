/**
 * The Fleet table: sortable headers over one row per Cell, held still while the reader is in it.
 *
 * A header is a button inside a column header cell; Enter and Space activate it like a click. The
 * sorted column shows an arrow and carries `aria-sort`, the others say `none`. A folded column (kind,
 * source, lease age, Cell Wax) has no header, since its value sits on another cell's second line;
 * the "Sort by" control still offers it. From 1280 px, where its columns fit the pane, it is a
 * table in a box that scrolls down on its own with a sticky header; below that the stylesheet turns
 * it into stacked cards and hides the header row, and the one "Sort by" control is drawn in its
 * place, so the page never scrolls sideways. While the pointer or focus is inside the box, rows that
 * arrive wait behind a "N new rows, re-sort" button (design.md 4.3).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/table/`. Used by `fleet.tsx`, which owns the sort
 *   choice, the hold and the order of the rows.
 *
 * Key invariants:
 *   - Rows are keyed by Cell id and update in place.
 *   - Holds no state: the sort and the hold come in as props and events go out as callbacks.
 *   - The table has a caption (visible to assistive technology) and column headers with scope.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 7.
 */
import type { ReactElement } from 'react';
import { SortSelect, useMinWidth, WIDE_TABLE_MIN } from '../../../components';
import type { FleetRow } from '../slice';
import type { Hold } from '../use_hold';
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
      {column.pair ? <span className="fleet-pair"> / {column.pair}</span> : null}
    </th>
  );
}

const plural = (n: number): string => `${n} new row${n === 1 ? '' : 's'}`;

export function FleetTable(props: {
  rows: readonly FleetRow[];
  sort: SortState | null;
  onSort: (key: SortKey) => void;
  onSortChange: (sort: SortState | null) => void;
  hold: Hold;
}): ReactElement {
  const { hold } = props;
  const table = useMinWidth(WIDE_TABLE_MIN);
  return (
    <>
      {table ? null : (
        <SortSelect
          options={COLUMNS}
          sort={props.sort}
          onChange={props.onSortChange}
          none="Hive order"
        />
      )}
      {hold.fresh > 0 ? (
        <button type="button" className="button fleet-fresh" onClick={hold.refresh}>
          {`${plural(hold.fresh)}, re-sort`}
        </button>
      ) : null}
      <div
        className="fleet-scroll"
        onPointerEnter={hold.hold}
        onPointerLeave={hold.release}
        onFocus={hold.hold}
        onBlur={(event) => {
          // Focus moving to another row keeps the hold; leaving the box releases it.
          if (!event.currentTarget.contains(event.relatedTarget)) hold.release();
        }}
      >
        <table className="fleet-table">
          <caption className="sr-only">Cells in the Hive</caption>
          <thead>
            <tr>
              {COLUMNS.filter((column) => !column.folded).map((column) => (
                <Header key={column.key} column={column} sort={props.sort} onSort={props.onSort} />
              ))}
              <th scope="col" className="fleet-plain">
                Links
              </th>
            </tr>
          </thead>
          <tbody>
            {props.rows.map((row) => (
              <FleetRowView key={row.id} row={row} cards={!table} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
