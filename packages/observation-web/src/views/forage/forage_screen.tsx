/**
 * The Forage view: how the Hive's capacity is divided, live, at `#/forage`.
 *
 * It shows the shared pool and each host's capacity (cores, memory, GPUs, model seats and sub-bee
 * slots, in use, granted, reserved and free), the flow from the Queen's pool to each Warden's grants
 * and each grant's spend, spend against the grants' caps, every live grant with those nearing
 * expiry first, the Forage requests with the Queen's decision, the Wardens, and the Forage map of
 * model sources. It seeds from three reads and then redraws from each ledger frame on the forage
 * stream, so nothing needs a refresh. The flow's unit (seats, sub-bees, spend, tokens) is a switch.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/`. Chosen by the outlet in `src/shell/` when the
 *   route is `forage`. Seeds by `GET /v1/forage`, `/v1/wardens` and `/v1/llm`; live on
 *   `/v1/forage/stream`.
 *
 * Key invariants:
 *   - Read-only: it reads the ledger and writes only its own slice.
 *   - A refusal (no `observe`) is the error table's sentence through `ErrorNotice`, never an
 *     empty screen.
 *   - Everything drawn comes from the generated types; a figure the schema lacks is not drawn.
 *
 * See Also:
 *   - docs/observation/design.md sections 4.7 and 5.3; src/views/forage/README.md.
 */
import { useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import { ErrorNotice, ViewTitle } from '../../components';
import { CapacityDiagram } from './capacity/diagram';
import { hostBlocks } from './capacity/model';
import { ForageFlow } from './flow/diagram';
import { flowModel } from './flow/model';
import type { FlowUnit } from './flow/model';
import { UnitSwitch } from './flow/unit_switch';
import './forage.css';
import type { ForageState } from './slice';
import { GrantsTable } from './tables/grants_table';
import { MapTable } from './tables/map_table';
import { RequestsTable } from './tables/requests_table';
import { SpendGauges } from './tables/spend_gauges';
import { WardensTable } from './tables/wardens_table';
import { useForage } from './use_forage';
import { useNow } from './use_now';

function Section(props: { title: string; empty?: string; children?: ReactNode }): ReactElement {
  return (
    <section className="forage-section">
      <h2>{props.title}</h2>
      {props.empty ? <p className="forage-note">{props.empty}</p> : props.children}
    </section>
  );
}

function Diagrams(props: { state: ForageState; nowMs: number }): ReactElement {
  const { state, nowMs } = props;
  const [unit, setUnit] = useState<FlowUnit>('seats');
  const blocks = hostBlocks(state);
  const flow = flowModel({ unit, nowMs, ...state });
  return (
    <div className="forage-panes">
      <Section title="Capacity">
        {state.capacities.length === 0 ? (
          <p className="forage-note">
            No Cell has reported capacity yet. A host appears here when a Cell reports its cores and
            memory.
          </p>
        ) : null}
        {blocks.length > 0 ? <CapacityDiagram blocks={blocks} /> : null}
      </Section>
      <Section title="Flow">
        <UnitSwitch unit={unit} onChange={setUnit} />
        <ForageFlow model={flow} />
      </Section>
    </div>
  );
}

function Tables(props: { state: ForageState; nowMs: number }): ReactElement {
  const { state, nowMs } = props;
  return (
    <>
      <Section
        title="Grants"
        empty={
          state.grants.length === 0
            ? 'No grants yet. A grant appears here when the Queen leases Forage to a Warden.'
            : undefined
        }
      >
        <GrantsTable grants={state.grants} nowMs={nowMs} />
      </Section>
      <Section
        title="Spend against grant caps"
        empty={
          state.grants.length === 0 ? 'Nothing is granted, so nothing has been spent.' : undefined
        }
      >
        <SpendGauges grants={state.grants} />
      </Section>
      <Section
        title="Requests"
        empty={
          state.requests.length === 0
            ? 'No requests since this view opened. A request appears here when a Warden asks for Forage beyond its grant.'
            : undefined
        }
      >
        <RequestsTable requests={state.requests} />
      </Section>
      {state.wardens.length > 0 ? (
        <Section title="Wardens">
          <WardensTable wardens={state.wardens} />
        </Section>
      ) : null}
      {state.llm ? (
        <Section title="Forage map">
          <MapTable llm={state.llm} />
        </Section>
      ) : null}
    </>
  );
}

export function ForageScreen(): ReactElement {
  const state = useForage();
  const nowMs = useNow();
  return (
    <>
      <ViewTitle title="Forage" />
      {state.error ? <ErrorNotice error={state.error} /> : null}
      {!state.seeded && !state.error ? <p aria-busy="true">Reading Forage</p> : null}
      {state.seeded ? (
        <>
          <Diagrams state={state} nowMs={nowMs} />
          <Tables state={state} nowMs={nowMs} />
        </>
      ) : null}
    </>
  );
}
