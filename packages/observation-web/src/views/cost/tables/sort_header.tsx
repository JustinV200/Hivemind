/**
 * `SortHeader`: a column header that sorts its table when clicked or activated by the keyboard.
 *
 * The header holds a real button, so Enter and Space work without extra code, and the `th` carries
 * `aria-sort` so a screen reader hears which column is sorted and which way. The arrow is drawn in
 * text beside the label, so the direction is never colour alone. A column that cannot sort is a
 * plain header.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/tables/`. Used by `cost_table.tsx`; holds no state
 *   (the table keeps the sorted column and direction).
 *
 * Key invariants:
 *   - `aria-sort` is `ascending` or `descending` on the sorted column and `none` on the others.
 *
 * See Also:
 *   - docs/observation/design.md section 4.3 (tables).
 */
import type { ReactElement } from 'react';

export type Direction = 'ascending' | 'descending';

export function SortHeader(props: {
  label: string;
  sorted: Direction | null;
  onSort: (() => void) | null;
}): ReactElement {
  if (!props.onSort) return <th scope="col">{props.label}</th>;
  const arrow = props.sorted === 'ascending' ? ' ▲' : props.sorted === 'descending' ? ' ▼' : '';
  return (
    <th scope="col" aria-sort={props.sorted ?? 'none'}>
      <button type="button" className="cost-sort" onClick={props.onSort}>
        {props.label}
        {arrow}
      </button>
    </th>
  );
}
