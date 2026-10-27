/**
 * `SlotsTable`: each model slot's spend, its hosted share and what moving it local would save.
 *
 * The saving is the hosted spend of the slot, the money that would be zero had a local model served
 * the same calls, so it is an upper bound and the heading says so. The hosted share is a `Gauge` of
 * calls (calls through a provider that charged, out of all the slot's calls), with the same numbers
 * in text beside the bar. It counts calls, not dollars, because hosted spend is all of a slot's
 * spend and a share of it would always read 100 percent. The bar is a share, not a limit, so it
 * keeps a neutral fill: a slot served wholly by a provider is a fact, not a fault.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/tables/`. Drawn by `cost_screen.tsx` from the
 *   slice's snapshot.
 *
 * Key invariants:
 *   - A slot with no calls draws an empty bar and 0 percent (the gauge never divides by zero).
 *   - Read-only; the only state is the sort (in `CostTable`).
 *
 * See Also:
 *   - src/components/gauge.tsx for the bar.
 *   - docs/observation/design.md sections 4.2 and 5.8.
 */
import type { ReactElement } from 'react';
import { Gauge } from '../../../components';
import { sharePercent, usd } from '../format';
import type { CostSnapshot } from '../slice';
import { CostTable } from './cost_table';
import type { Column } from './cost_table';
import { slotRows } from './rows';
import type { SlotRow } from './rows';

const COLUMNS: readonly Column<SlotRow>[] = [
  { id: 'slot', label: 'Slot', cell: (row) => row.slot, sortBy: (row) => row.slot },
  { id: 'spend', label: 'Spend', cell: (row) => usd(row.spend), sortBy: (row) => row.spend },
  { id: 'hosted', label: 'Hosted', cell: (row) => usd(row.hosted), sortBy: (row) => row.hosted },
  {
    id: 'saving',
    label: 'If local, saves (at most)',
    cell: (row) => usd(row.saving),
    sortBy: (row) => row.saving,
  },
  {
    id: 'share',
    label: 'Hosted calls',
    cell: (row) => (
      <Gauge
        used={row.hostedCalls}
        budget={row.calls}
        label={`Hosted share of ${row.slot} calls`}
        tone="neutral"
      />
    ),
    sortBy: (row) => sharePercent(row.hostedCalls, row.calls),
  },
];

export function SlotsTable(props: { cost: CostSnapshot }): ReactElement {
  return (
    <CostTable
      caption="Spend per slot, and what moving it local would save"
      columns={COLUMNS}
      rows={slotRows(props.cost)}
      keyOf={(row) => row.key}
      initial={{ column: 'saving', descending: true }}
    />
  );
}
