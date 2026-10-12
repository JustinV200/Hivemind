/**
 * `GoalsTable`: each goal's spend, calls and tokens, split by slot and by provider.
 *
 * Goals come costliest first and sort by goal, spend, calls or tokens. The unattributed row (calls
 * that named no goal, directly or through a grant) is always last and a sentence under the table
 * says what it is, so nobody reads it as a goal. The by-slot and by-provider cells are the goal's
 * spend split, biggest first, as words and dollars.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/tables/`. Drawn by `cost_screen.tsx` from the
 *   slice's snapshot.
 *
 * Key invariants:
 *   - The unattributed row is never given a goal id and never moves above a real goal.
 *   - Read-only; the only state is the sort (in `CostTable`).
 *
 * See Also:
 *   - src/views/cost/tables/rows.ts for the row model.
 *   - docs/observation/design.md section 5.8.
 */
import type { ReactElement } from 'react';
import { IdToken } from '../../../components';
import { count, usd } from '../format';
import type { CostSnapshot } from '../slice';
import { CostTable } from './cost_table';
import type { Column } from './cost_table';
import { goalRows, UNATTRIBUTED } from './rows';
import type { GoalRow, Split } from './rows';

function SplitList(props: { splits: readonly Split[] }): ReactElement {
  return (
    <ul className="cost-split">
      {props.splits.map((split) => (
        <li key={split.name}>
          <span>{split.name}</span> {usd(split.usd)}
        </li>
      ))}
    </ul>
  );
}

const tokenTotal = (row: GoalRow): number => row.tokens.input + row.tokens.output;

const COLUMNS: readonly Column<GoalRow>[] = [
  {
    id: 'goal',
    label: 'Goal',
    cell: (row) => (row.goal === null ? UNATTRIBUTED : <IdToken id={row.goal} />),
    sortBy: (row) => row.goal ?? '',
  },
  { id: 'spend', label: 'Spend', cell: (row) => usd(row.spend), sortBy: (row) => row.spend },
  { id: 'calls', label: 'Calls', cell: (row) => count(row.calls), sortBy: (row) => row.calls },
  {
    id: 'tokens',
    label: 'Tokens in / out',
    cell: (row) => `${count(row.tokens.input)} / ${count(row.tokens.output)}`,
    sortBy: tokenTotal,
  },
  { id: 'slot', label: 'By slot', cell: (row) => <SplitList splits={row.bySlot} /> },
  { id: 'provider', label: 'By provider', cell: (row) => <SplitList splits={row.byProvider} /> },
];

export function GoalsTable(props: { cost: CostSnapshot }): ReactElement {
  const rows = goalRows(props.cost);
  return (
    <>
      <CostTable
        caption="Spend per goal"
        columns={COLUMNS}
        rows={rows}
        keyOf={(row) => row.key}
        initial={{ column: 'spend', descending: true }}
        last={(row) => row.goal === null}
      />
      {rows.some((row) => row.goal === null) ? (
        <p className="cost-note">
          Unattributed is spend whose call named no goal, directly or through a grant; it is never
          guessed, and it moves under its goal once the Fanner lanes carry one.
        </p>
      ) : null}
    </>
  );
}
