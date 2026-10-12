/**
 * `SortHeader`: a queue column header that sorts the table when clicked or activated by keyboard.
 *
 * The header holds a real button, so Enter and Space work without extra code, and the `th` carries
 * `aria-sort` so a screen reader hears which column is sorted and which way. The arrow is drawn in
 * text beside the label, so the direction is never colour alone.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/queue/`. Used by `queue_table.tsx`; holds no
 *   state (the table keeps the sorted column and direction).
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
  onSort: () => void;
}): ReactElement {
  const arrow = props.sorted === 'ascending' ? ' ▲' : props.sorted === 'descending' ? ' ▼' : '';
  return (
    <th scope="col" aria-sort={props.sorted ?? 'none'}>
      <button type="button" className="capping-sort" onClick={props.onSort}>
        {props.label}
        {arrow}
      </button>
    </th>
  );
}
