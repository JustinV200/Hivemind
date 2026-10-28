/**
 * `SortSelect`: the one "Sort by" control a sortable table draws while its rows are cards.
 *
 * On a phone a table is a stack of cards and its header row is hidden, so the sort needs a control
 * of its own. One select names the column and one button flips the direction: two controls instead
 * of a row of header chips, and they read as a sentence. From the width where the table is a table
 * the view draws its header buttons instead; both drive the same sort state, so the order survives
 * a resize. The Fleet, the Forage map, the Cost tables and the Capping queue all draw this one, so
 * every sortable table sorts the same way on a phone (design.md section 2).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/components/`. Drawn by a view's table component above its
 *   table, only while `useMinWidth` says the rows are cards; holds no state.
 *
 * Key invariants:
 *   - The direction button exists only while a column is chosen, and says its direction in words.
 *   - The options are the caller's sortable columns, in the caller's order.
 *   - An unsorted choice exists only when the caller names one (the Fleet's "Hive order").
 *
 * See Also:
 *   - docs/observation/design.md sections 2 and 4.3.
 *   - src/components/use_min_width.ts for the widths at which a table is a table.
 */
import type { ReactElement } from 'react';

export type SortDirection = 'ascending' | 'descending';

export interface SortOption<K extends string> {
  key: K;
  label: string;
}

export interface SortChoice<K extends string> {
  key: K;
  direction: SortDirection;
}

// The select's value for "no column chosen"; never a column key.
const NONE = '';

export function SortSelect<K extends string>(props: {
  options: readonly SortOption<K>[];
  sort: SortChoice<K> | null;
  onChange: (sort: SortChoice<K> | null) => void;
  // The label of the unsorted choice; without one the table is always sorted by some column.
  none?: string;
  // The direction a newly chosen column starts in; ascending unless the caller says otherwise.
  startWith?: (key: K) => SortDirection;
}): ReactElement {
  const { sort, onChange } = props;
  const flipped = (): SortChoice<K> | null =>
    sort && {
      key: sort.key,
      direction: sort.direction === 'ascending' ? 'descending' : 'ascending',
    };
  // The value is one of the options' keys or the none marker: the select itself guarantees it.
  const choose = (value: string): void => {
    if (value === NONE) return onChange(null);
    const key = value as K;
    onChange({ key, direction: props.startWith?.(key) ?? 'ascending' });
  };
  return (
    <div className="sort-select">
      <label>
        Sort by{' '}
        <select value={sort?.key ?? NONE} onChange={(event) => choose(event.target.value)}>
          {props.none !== undefined ? <option value={NONE}>{props.none}</option> : null}
          {props.options.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
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
