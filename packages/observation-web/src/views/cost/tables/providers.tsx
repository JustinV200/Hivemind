/**
 * `ProvidersTable`: each model provider's calls, tokens and spend.
 *
 * A provider is named as the manifest names it. A local provider shows no spend, so its row reads
 * `$0.0000` with its calls and tokens, which is the point: it served them for free.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/tables/`. Drawn by `cost_screen.tsx` from the
 *   slice's snapshot.
 *
 * Key invariants:
 *   - Read-only; the only state is the sort (in `CostTable`).
 *
 * See Also:
 *   - src/views/cost/tables/rows.ts for the row model.
 *   - docs/observation/design.md section 5.8.
 */
import type { ReactElement } from 'react';
import { count, usd } from '../format';
import type { CostSnapshot } from '../slice';
import { CostTable } from './cost_table';
import type { Column } from './cost_table';
import { providerRows } from './rows';
import type { ProviderRow } from './rows';

const COLUMNS: readonly Column<ProviderRow>[] = [
  {
    id: 'provider',
    label: 'Provider',
    cell: (row) => row.provider,
    sortBy: (row) => row.provider,
  },
  { id: 'calls', label: 'Calls', cell: (row) => count(row.calls), sortBy: (row) => row.calls },
  {
    id: 'tokens',
    label: 'Tokens in / out',
    cell: (row) => `${count(row.tokens.input)} / ${count(row.tokens.output)}`,
    sortBy: (row) => row.tokens.input + row.tokens.output,
  },
  { id: 'spend', label: 'Spend', cell: (row) => usd(row.spend), sortBy: (row) => row.spend },
];

export function ProvidersTable(props: { cost: CostSnapshot }): ReactElement {
  return (
    <CostTable
      caption="Spend per provider"
      columns={COLUMNS}
      rows={providerRows(props.cost)}
      keyOf={(row) => row.key}
      initial={{ column: 'spend', descending: true }}
    />
  );
}
