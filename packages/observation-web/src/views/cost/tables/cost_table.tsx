/**
 * `CostTable`: one sortable table, drawn from a list of columns and rows.
 *
 * The goal, slot and provider tables differ only in their columns, so they share this one. A column
 * sorts when its header is clicked or activated by Enter or Space; the sorted column shows an arrow
 * and `aria-sort`, and choosing it again flips the direction. The sort is stable and ties break on
 * the row's key, so the order is deterministic. A row the caller marks `last` always stays at the
 * bottom whatever the sort (the unattributed goal). Under 720 px the table becomes stacked cards,
 * each cell keeping its label from `data-label`, and the shared "Sort by" control stands in for
 * the hidden header row.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/tables/`. Used by `goals.tsx`, `slots.tsx` and
 *   `providers.tsx`; the only state is the sort the reader chose.
 *
 * Key invariants:
 *   - Read-only: sorting reorders what is drawn and nothing else.
 *   - Rows are keyed by `keyOf`, so a row updates in place when its figures change.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3.
 */
import { useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { NARROW_TABLE_MIN, SortSelect, useMinWidth } from '../../../components';
import { SortHeader } from './sort_header';

export interface Column<R> {
  id: string;
  label: string;
  cell: (row: R) => ReactNode;
  // The value the column sorts by; a column without one is not sortable.
  sortBy?: (row: R) => string | number;
}

interface Sort {
  column: string;
  descending: boolean;
}

const compare = (a: string | number, b: string | number): number => (a < b ? -1 : a > b ? 1 : 0);

export function CostTable<R>(props: {
  caption: string;
  columns: readonly Column<R>[];
  rows: readonly R[];
  keyOf: (row: R) => string;
  initial: Sort;
  last?: (row: R) => boolean;
}): ReactElement {
  const { caption, columns, rows, keyOf, last } = props;
  const [sort, setSort] = useState<Sort>(props.initial);
  const by = columns.find((c) => c.id === sort.column)?.sortBy;
  const sign = sort.descending ? -1 : 1;
  // Pinned rows are split off first so no sort can lift them out of the last place.
  const pinned = rows.filter((row) => last?.(row));
  const body = rows
    .filter((row) => !last?.(row))
    .sort((a, b) => (by ? sign * compare(by(a), by(b)) : 0) || compare(keyOf(a), keyOf(b)));
  // Choosing the sorted column again reverses it; choosing another starts ascending.
  const choose = (column: string): void =>
    setSort((now) => ({ column, descending: now.column === column && !now.descending }));
  const wide = useMinWidth(NARROW_TABLE_MIN);
  return (
    <>
      {wide ? null : (
        <SortSelect
          options={columns.filter((c) => c.sortBy).map((c) => ({ key: c.id, label: c.label }))}
          sort={{ key: sort.column, direction: sort.descending ? 'descending' : 'ascending' }}
          onChange={(next) => {
            if (next) setSort({ column: next.key, descending: next.direction === 'descending' });
          }}
        />
      )}
      <table className="cost-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <SortHeader
                key={c.id}
                label={c.label}
                sorted={
                  sort.column === c.id ? (sort.descending ? 'descending' : 'ascending') : null
                }
                onSort={c.sortBy ? () => choose(c.id) : null}
              />
            ))}
          </tr>
        </thead>
        <tbody>
          {[...body, ...pinned].map((row) => (
            <tr key={keyOf(row)}>
              {columns.map((c) => (
                <td key={c.id} data-label={c.label}>
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
