/**
 * The Cost view: what the Hive has spent, and what moving a slot local would save, at `#/cost`.
 *
 * It shows a strip of totals (total spend, hosted spend, the local saving), a table of spend per
 * goal split by slot and provider, a table per slot with the saving and a gauge of its hosted
 * share, and a table per provider. It seeds from `GET /v1/cost` and then folds each completed
 * model call from the trail into the figures, so nothing needs a refresh.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/`. Chosen by the outlet in `src/shell/` when the
 *   route is `cost`. Seeds by `GET /v1/cost`; live on `/v1/trail/stream`.
 *
 * Key invariants:
 *   - Read-only: it reads the trail and writes only its own slice.
 *   - A refusal (no `observe`) is the error table's sentence through `ErrorNotice`, never an
 *     empty screen.
 *   - Everything drawn comes from the generated types; a figure the schema lacks is not drawn.
 *
 * See Also:
 *   - docs/observation/design.md section 5.8; src/views/cost/README.md.
 */
import type { ReactElement, ReactNode } from 'react';
import { EmptyState, ErrorNotice, ViewTitle } from '../../components';
import './cost.css';
import type { CostSnapshot } from './slice';
import { GoalsTable } from './tables/goals';
import { ProvidersTable } from './tables/providers';
import { SlotsTable } from './tables/slots';
import { Totals } from './tables/totals';
import { useCost } from './use_cost';

function Section(props: { title: string; children: ReactNode }): ReactElement {
  return (
    <section className="cost-section">
      <h2 className="section-heading">{props.title}</h2>
      {props.children}
    </section>
  );
}

function Spend(props: { cost: CostSnapshot }): ReactElement {
  const { cost } = props;
  // No call is on record yet: say what would fill the view rather than draw empty tables.
  if (cost.goals.length === 0) {
    return (
      <EmptyState>
        No spend has been recorded. A goal&apos;s spend appears here when a model call completes.
      </EmptyState>
    );
  }
  return (
    <>
      <Totals cost={cost} />
      <Section title="Goals">
        <GoalsTable cost={cost} />
      </Section>
      <Section title="Slots">
        <SlotsTable cost={cost} />
      </Section>
      <Section title="Providers">
        <ProvidersTable cost={cost} />
      </Section>
    </>
  );
}

export function CostScreen(): ReactElement {
  const state = useCost();
  return (
    <>
      <ViewTitle title="Cost" />
      {state.error ? <ErrorNotice error={state.error} /> : null}
      {!state.seeded && !state.error ? <p aria-busy="true">Reading the cost</p> : null}
      {state.seeded ? <Spend cost={state.cost} /> : null}
    </>
  );
}
