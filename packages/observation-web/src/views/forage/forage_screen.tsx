/**
 * The Forage view: how the Hive's capacity is divided, live, at `#/forage`.
 *
 * It shows the shared pool beside the flow from the Queen's pool to each Warden's grants and each
 * grant's spend (the unit, seats, sub-bees, spend or tokens, is a switch), then each host's
 * capacity (cores, memory, GPUs, model seats and sub-bee slots, in use, granted, reserved and
 * free) as a grid of compact cards named from the fleet, then spend against the grants' caps,
 * every live grant with those nearing expiry first, the Forage requests with the Queen's decision,
 * the Wardens, and the Forage map of model sources (those last three closed behind a disclosure on
 * a phone, where the page would otherwise run to several screens). It seeds from three reads and
 * then redraws from each ledger frame on the forage stream, so nothing needs a refresh.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/`. Chosen by the outlet in `src/shell/` when the
 *   route is `forage`. Seeds by `GET /v1/forage`, `/v1/wardens` and `/v1/llm`; live on
 *   `/v1/forage/stream`. Reads the fleet slice, when the fleet has been seeded, for host names.
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
import { useMemo, useState } from 'react';
import type { ReactElement, ReactNode } from 'react';
import {
  EmptyState,
  ErrorNotice,
  RAIL_MIN,
  useMinWidth,
  VIEW_HINTS,
  ViewTitle,
} from '../../components';
import { useSlice } from '../../store';
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

const POOL_ID = 'shared-pool';

function Section(props: {
  title: string;
  empty?: string;
  // A secondary section is a closed disclosure on a phone, so the page stays short there.
  secondary?: boolean;
  children?: ReactNode;
}): ReactElement {
  const wide = useMinWidth(RAIL_MIN);
  const body = props.empty ? <EmptyState>{props.empty}</EmptyState> : props.children;
  if (props.secondary && !wide) {
    return (
      <details className="forage-section">
        <summary>
          <h2 className="section-heading">{props.title}</h2>
        </summary>
        {body}
      </details>
    );
  }
  return (
    <section className="forage-section">
      <h2 className="section-heading">{props.title}</h2>
      {body}
    </section>
  );
}

const NO_SOURCES = 'No model sources yet. One appears here when a provider is configured.';

// The Cell names the fleet knows, so a host card reads "hive-stand" rather than a 24 character id.
function useHostNames(): ReadonlyMap<string, string> {
  const { rows } = useSlice('fleet');
  return useMemo(() => new Map(rows.map((row) => [row.id, row.name])), [rows]);
}

function Diagrams(props: { state: ForageState; nowMs: number }): ReactElement {
  const { state, nowMs } = props;
  const [unit, setUnit] = useState<FlowUnit>('seats');
  const names = useHostNames();
  const blocks = hostBlocks(state);
  const pool = blocks.filter((block) => block.id === POOL_ID);
  const hosts = blocks.filter((block) => block.id !== POOL_ID);
  const flow = flowModel({ unit, nowMs, ...state });
  return (
    <>
      <div className="forage-panes">
        <Section title="Shared pool">
          {pool.length > 0 ? (
            <CapacityDiagram blocks={pool} caption="Shared pool" />
          ) : (
            <EmptyState>
              No pool figures yet. They appear once the Queen reports her headroom.
            </EmptyState>
          )}
        </Section>
        <Section title="Flow">
          <UnitSwitch unit={unit} onChange={setUnit} />
          <ForageFlow model={flow} />
        </Section>
      </div>
      <Section title="Hosts">
        {hosts.length > 0 ? (
          // The legend is drawn once: under the pool's figure, or here when there is no pool.
          <CapacityDiagram blocks={hosts} names={names} legend={pool.length === 0} />
        ) : (
          <EmptyState>
            No Cell has reported capacity yet. A host appears here when a Cell reports its cores and
            memory.
          </EmptyState>
        )}
      </Section>
    </>
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
        secondary
        empty={
          state.requests.length === 0
            ? 'No requests since this view opened. A request appears here when a Warden asks for Forage beyond its grant.'
            : undefined
        }
      >
        <RequestsTable requests={state.requests} />
      </Section>
      {state.wardens.length > 0 ? (
        <Section title="Wardens" secondary>
          <WardensTable wardens={state.wardens} />
        </Section>
      ) : null}
      {state.llm ? (
        <Section
          title="Forage map"
          secondary
          // A map with no source would draw a bare header row; say what would fill it instead.
          empty={
            state.llm.slots.length === 0 && state.llm.providers.length === 0
              ? NO_SOURCES
              : undefined
          }
        >
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
      <ViewTitle title="Forage" hint={VIEW_HINTS.forage} />
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
