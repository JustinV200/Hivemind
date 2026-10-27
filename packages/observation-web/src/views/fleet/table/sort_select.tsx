/**
 * `SortSelect`: the phone's one "Sort by" control, in place of a row of fourteen header chips.
 *
 * Under 1080 px the table is a stack of cards and its header row is hidden, so the sort needs a
 * control of its own. One select names the column (or the Hive's own order) and one button flips
 * the direction, which is two controls instead of fourteen and reads as a sentence. From 1080 px
 * the stylesheet hides this and the header buttons sort instead; both drive the same sort state.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/fleet/table/`. Drawn by `fleet_table.tsx` above the
 *   table; holds no state.
 *
 * Key invariants:
 *   - The direction button exists only while a column is chosen, and says its direction in words.
 *   - The options are the same columns, in the same order, as the header.
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 4.3.
 */
import type { ReactElement } from 'react';
import { COLUMNS } from './sorting';
import type { SortKey, SortState } from './sorting';

const NONE = '';

export function SortSelect(props: {
  sort: SortState | null;
  onChange: (sort: SortState | null) => void;
}): ReactElement {
  const { sort, onChange } = props;
  const flipped = (): SortState | null =>
    sort && {
      key: sort.key,
      direction: sort.direction === 'ascending' ? 'descending' : 'ascending',
    };
  return (
    <div className="fleet-sort-select">
      <label>
        Sort by{' '}
        <select
          value={sort?.key ?? NONE}
          onChange={(event) => {
            const key = event.target.value;
            onChange(key === NONE ? null : { key: key as SortKey, direction: 'ascending' });
          }}
        >
          <option value={NONE}>Hive order</option>
          {COLUMNS.map((column) => (
            <option key={column.key} value={column.key}>
              {column.label}
            </option>
          ))}
        </select>
      </label>
      {sort ? (
        <button type="button" className="button" onClick={() => onChange(flipped())}>
          {sort.direction === 'ascending' ? '▲ ascending' : '▼ descending'}
        </button>
      ) : null}
    </div>
  );
}
