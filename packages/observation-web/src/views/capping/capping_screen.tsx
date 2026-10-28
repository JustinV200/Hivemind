/**
 * The Capping view: the gate nothing lands past unchecked, Hive-wide and live, at `#/capping`.
 *
 * Capping (the gate every action passes) is shown as a rates strip per risk tier, then the queue
 * of proposals filterable by tier and state with the gate's verdicts, the reasons for them and the
 * rollbacks, then the sampled audits. Every item links to its bee's thoughts; an Exoskeleton
 * action carries a muted note that its recording is not available yet, because the Capping schema
 * has no recording reference. It seeds from `GET /v1/capping` and folds each `capping.*` frame of
 * the trail stream at once, so nothing needs a refresh.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/`. Chosen by the outlet in `src/shell/` when the
 *   route is `capping`. Built to docs/observation/design.md section 5.9.
 *
 * Key invariants:
 *   - Read-only: it reads the queue and writes only its own slice.
 *   - A refusal (no `observe`) is the error table's sentence through `ErrorNotice`, never an empty
 *     screen; a queue with nothing in it says what would fill it.
 *   - Everything drawn comes from the generated types; a figure the schema lacks is not drawn.
 *
 * See Also:
 *   - src/views/capping/README.md, src/views/capping/use_capping.ts for how it stays live.
 */
import type { ReactElement, ReactNode } from 'react';
import { EmptyState, ErrorNotice, ViewTitle } from '../../components';
import './capping.css';
import { AuditsTable } from './audits/audits_table';
import { QueueFilters, useQueueFilter } from './queue/filters';
import { QueueTable } from './queue/queue_table';
import { filterRows, queueRows } from './queue/rows';
import { RatesStrip } from './rates/rates_strip';
import type { CappingState } from './slice';
import { useCapping } from './use_capping';

function Section(props: { title: string; children: ReactNode }): ReactElement {
  return (
    <section className="capping-section" aria-label={props.title}>
      <h2 className="section-heading">{props.title}</h2>
      {props.children}
    </section>
  );
}

function isEmpty(state: CappingState): boolean {
  return state.proposals.length === 0 && state.audits.length === 0 && state.counts.length === 0;
}

function Body(props: { state: CappingState }): ReactElement {
  const { state } = props;
  const filter = useQueueFilter();
  const rows = queueRows(state);
  const shown = filterRows(rows, filter.tiers, filter.states);
  return (
    <>
      <ViewTitle title="Capping">
        <QueueFilters rows={rows} filter={filter} />
      </ViewTitle>
      {state.error ? <ErrorNotice error={state.error} /> : null}
      <RatesStrip state={state} />
      <Section title="Queue">
        {shown.length === 0 ? (
          <EmptyState>No proposals match these filters.</EmptyState>
        ) : (
          <QueueTable rows={shown} />
        )}
      </Section>
      <Section title="Sampled audits">
        {state.audits.length === 0 ? (
          <EmptyState>
            No audits yet. An audit appears here when the judge takes a second look at a finished
            proposal of a tier the gate cannot check in real time.
          </EmptyState>
        ) : (
          <AuditsTable audits={state.audits} />
        )}
      </Section>
    </>
  );
}

// Before the first answer: the title and either the refusal or the wait, never a blank screen.
function Waiting(props: { error: unknown }): ReactElement {
  return (
    <>
      <ViewTitle title="Capping" />
      {props.error ? (
        <ErrorNotice error={props.error} />
      ) : (
        <p aria-busy="true">Reading the Capping queue</p>
      )}
    </>
  );
}

function Nothing(props: { error: unknown }): ReactElement {
  return (
    <>
      <ViewTitle title="Capping" />
      {props.error ? <ErrorNotice error={props.error} /> : null}
      <EmptyState>
        Nothing has been proposed yet. A proposal appears here when a bee asks the gate to let it do
        something with a side effect outside its scratch directory.
      </EmptyState>
    </>
  );
}

export function CappingScreen(): ReactElement {
  const state = useCapping();
  if (!state.seeded) return <Waiting error={state.error} />;
  return isEmpty(state) ? <Nothing error={state.error} /> : <Body state={state} />;
}
