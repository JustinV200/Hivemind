/**
 * Test builders and a mount helper for the Forage view.
 *
 * The generated schema types have many required members that a test about one grant does not care
 * about. These builders fill them with plain defaults and let a test override only what it is
 * about, so each test reads as its point and still type-checks against the generated types. The
 * mount helper builds a store holding the app's slices with the Forage slice (so it works before
 * and after `src/app.tsx` lists it), a client on the fake browser with a stored session, and
 * renders the Forage screen directly under the provider.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the Forage slice, layout, table and view tests.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks the builders at
 *     compile time.
 *   - Times are built from the real clock (`inMinutes`), because the view reads `Date.now()`.
 *   - No request is made that the test did not route; an unrouted read answers 404.
 *
 * See Also:
 *   - tests/support/fake_browser.ts for the fake browser used here.
 */
import { render } from '@testing-library/react';
import { createElement } from 'react';
import { APP_SLICES } from '../../../src/app';
import { createLandingBoard } from '../../../src/landing_board/client';
import type { components } from '../../../src/landing_board/types';
import { createHiveStore, HiveProvider } from '../../../src/store';
import type { Hive } from '../../../src/store';
import { forageSlice } from '../../../src/views/forage';
import { ForageScreen } from '../../../src/views/forage';
import { createFakeBrowser, flush } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];

export const GIB = 1024 ** 3;

export const inMinutes = (minutes: number): string =>
  new Date(Date.now() + minutes * 60_000).toISOString();

export function capacity(over: Partial<Schemas['CapacityView']> = {}): Schemas['CapacityView'] {
  return {
    cell_id: 'cell_1',
    cores: 8,
    cpu_load: 0.5,
    disk_bytes: 500 * GIB,
    disk_free_bytes: 250 * GIB,
    gpus: 0,
    local_seats: 4,
    max_sub_bees: 6,
    memory_bytes: 16 * GIB,
    memory_free_bytes: 8 * GIB,
    ...over,
  };
}

export function grant(over: Partial<Schemas['GrantView']> = {}): Schemas['GrantView'] {
  return {
    id: 'grant_1',
    holder: 'warden_1',
    cell_id: 'cell_1',
    task_id: 'task_1',
    revision: 1,
    state: 'ACTIVE',
    max_sub_bees: 2,
    seats: 2,
    token_budget: 1000,
    tokens_spent: 100,
    spend_budget: 5,
    spent: 1,
    expires_at: inMinutes(60),
    ...over,
  };
}

export const headroom = (over: Partial<Schemas['HeadroomView']> = {}): Schemas['HeadroomView'] => ({
  shared_seats: 6,
  sub_bees: 10,
  ...over,
});

export const reserve = (over: Partial<Schemas['ReserveView']> = {}): Schemas['ReserveView'] => ({
  headroom_fraction: 0.1,
  memory_bytes: 2 * GIB,
  seats: 2,
  ...over,
});

export function forageView(over: Partial<Schemas['ForageView']> = {}): Schemas['ForageView'] {
  return {
    capacities: [capacity()],
    grants: [grant()],
    headroom: headroom(),
    reserve: reserve(),
    ...over,
  };
}

export function warden(over: Partial<Schemas['WardenView']> = {}): Schemas['WardenView'] {
  return {
    cell_id: 'cell_1',
    id: 'warden_1',
    last_heartbeat_at: '2026-01-01T00:00:00Z',
    live_grants: 1,
    missed_heartbeats: 0,
    offline: false,
    state: 'ACTIVE',
    sub_bees: 2,
    ...over,
  };
}

export function llm(over: Partial<Schemas['LlmView']> = {}): Schemas['LlmView'] {
  return {
    providers: [
      { clustered: false, failed_probes: 0, health: 'HEALTHY', kind: 'anthropic', name: 'cloud' },
      {
        clustered: false,
        failed_probes: 0,
        health: 'DEGRADED',
        kind: 'openai_compat',
        name: 'local',
      },
    ],
    queen_mode: 'RUNNING',
    slots: [
      {
        effort: 'HIGH',
        fallback: null,
        key: 'queen',
        model: 'big-1',
        provider: 'cloud',
        serves: 'QUEEN',
      },
      {
        effort: 'LOW',
        fallback: 'queen',
        key: 'worker',
        model: 'small-1',
        provider: 'local',
        serves: 'WORKER',
      },
    ],
    ...over,
  };
}

// One forage.* trail event; the payload carries ids, counts and reason codes only.
export function eventView(
  kind: string,
  subject: string,
  payload: Record<string, string | number | boolean | null> = {},
  id = `ev_${kind}_${subject}`,
): Schemas['TrailEventView'] {
  return {
    actor: 'queen',
    at: '2026-01-01T00:00:00Z',
    family: 'forage',
    id,
    kind,
    node_id: 'node_1',
    payload,
    subject_id: subject,
  };
}

export function forageFrame(
  event: Schemas['TrailEventView'],
  over: Partial<Schemas['ForageFrame']> = {},
): Schemas['ForageFrame'] {
  return { type: 'forage', event, grant: null, headroom: headroom(), ...over };
}

export interface Mounted {
  fake: FakeBrowser;
  store: Hive['store'];
}

export interface Reads {
  forage?: unknown;
  wardens?: unknown;
  llm?: unknown;
  status?: number;
  body?: unknown;
}

// Renders the Forage screen on the fake browser; resolves once its stream socket exists and is open.
export async function mountForage(reads: Reads = {}): Promise<Mounted> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  const status = reads.status ?? 200;
  fake.respond('GET /v1/forage', status, reads.body ?? reads.forage ?? forageView());
  fake.respond('GET /v1/wardens', status, reads.body ?? reads.wardens ?? { wardens: [warden()] });
  fake.respond('GET /v1/llm', status, reads.body ?? reads.llm ?? llm());
  const store = createHiveStore({ ...APP_SLICES, forage: forageSlice });
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => undefined });
  await client.session.restore();
  render(
    createElement(HiveProvider, { hive: { store, client }, children: createElement(ForageScreen) }),
  );
  await flush();
  fake.sockets.forEach((socket) => socket.open());
  await flush();
  return { fake, store };
}
