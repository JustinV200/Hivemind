/**
 * `MapTable`: the Forage map as a table, every source with what the Hive knows of it, sortable.
 *
 * A column sorts when its header is clicked or activated by Enter or Space; the sorted column shows
 * an arrow and `aria-sort`. Clicking the sorted column again flips its direction. Its eight columns
 * fit the pane from the rail width, so under 720 px the table becomes stacked cards (one source,
 * one card), each cell keeping its label, and the shared "Sort by" control stands in for the
 * hidden header row. Health is a word with a dot, never a colour alone.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/tables/`. Drawn by `forage_screen.tsx` from the
 *   slice's `llm`; the only state is the sort the reader chose.
 *
 * Key invariants:
 *   - Read-only: sorting reorders what is drawn and nothing else.
 *   - Rows are keyed by the binding, so a row updates in place when its data changes.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.3 and 5.3.
 */
import { useState } from 'react';
import type { ReactElement } from 'react';
import { NARROW_TABLE_MIN, plainCode, SortSelect, useMinWidth } from '../../../components';
import type { LlmView } from '../slice';
import { MAP_COLUMNS, mapRows, NONE, sortMapRows } from './map_rows';
import type { MapColumn, MapRow } from './map_rows';
import { SortHeader } from './sort_header';

interface Sort {
  column: MapColumn;
  descending: boolean;
}

// Health reads as a word with a dot; the dot is decoration and the word is the content. The
// word is the schema's code in lower case (`healthy`), the code on the title.
function Health(props: { code: string }): ReactElement {
  const tone = props.code === 'HEALTHY' ? 'ok' : props.code === 'DEGRADED' ? 'warn' : 'alarm';
  return (
    <span className="status" title={props.code}>
      <span className={`status-dot status-dot--${tone}`} aria-hidden="true" />
      <span>{plainCode(props.code)}</span>
    </span>
  );
}

// The row keeps the schema's codes so the sort is on them; the two that are codes (health and
// effort) are drawn as words here, the hyphen for a source that has neither.
function cell(row: MapRow, column: MapColumn): ReactElement | string {
  const value = row[column];
  if (value === NONE) return value;
  if (column === 'health') return <Health code={value} />;
  if (column === 'effort') return <span title={value}>{plainCode(value)}</span>;
  return value;
}

export function MapTable(props: { llm: LlmView }): ReactElement {
  const [sort, setSort] = useState<Sort>({ column: 'slot', descending: false });
  const rows = sortMapRows(mapRows(props.llm), sort.column, sort.descending);
  // Choosing the sorted column again reverses it; choosing another starts ascending.
  const choose = (column: MapColumn): void =>
    setSort((now) => ({ column, descending: now.column === column && !now.descending }));
  const wide = useMinWidth(NARROW_TABLE_MIN);
  return (
    <>
      {wide ? null : (
        <SortSelect
          options={MAP_COLUMNS.map(({ column, label }) => ({ key: column, label }))}
          sort={{ key: sort.column, direction: sort.descending ? 'descending' : 'ascending' }}
          onChange={(next) => {
            if (next) setSort({ column: next.key, descending: next.direction === 'descending' });
          }}
        />
      )}
      <table className="forage-table">
        <caption>Forage map: the model sources the Hive can reach</caption>
        <thead>
          <tr>
            {MAP_COLUMNS.map(({ column, label }) => (
              <SortHeader
                key={column}
                label={label}
                sorted={
                  sort.column === column ? (sort.descending ? 'descending' : 'ascending') : null
                }
                onSort={() => choose(column)}
              />
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              {MAP_COLUMNS.map(({ column, label }) => (
                <td key={column} data-label={label}>
                  {cell(row, column)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
