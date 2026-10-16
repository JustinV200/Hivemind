/**
 * Test builders and a mount helper for the Cost view.
 *
 * The generated schema types have many required members that a test about one line of spend does
 * not care about. These builders fill them with plain defaults and let a test override only what it
 * is about, so each test reads as its point and still type-checks against the generated types.
 * `costFrom` totals a list of lines into a whole `CostView` the way the Entrance's builder does, so
 * the fixtures stay consistent. The mount helper builds a store holding the app's slices with the
 * Cost slice (so it works before and after `src/app.tsx` lists it), a client on the fake browser
 * with a stored session, and renders the Cost screen directly under the provider.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the Cost slice, formatting and view tests.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks the builders at
 *     compile time.
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
import { CostScreen, costSlice } from '../../../src/views/cost';
import { createFakeBrowser, flush } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];

export function line(over: Partial<Schemas['CostLine']> = {}): Schemas['CostLine'] {
  return {
    goal_id: 'goal_1',
    slot: 'queen',
    provider: 'cloud',
    calls: 1,
    cost_usd: 0.01,
    input_tokens: 1000,
    output_tokens: 200,
    cached_tokens: 0,
    ...over,
  };
}

// Sums lines into a whole view as the Entrance's builder does: goals costliest first, null last.
export function costFrom(lines: Schemas['CostLine'][]): Schemas['CostView'] {
  const goalIds = [...new Set(lines.map((l) => l.goal_id))];
  const sum = (items: Schemas['CostLine'][]): number => items.reduce((n, l) => n + l.cost_usd, 0);
  const goals = goalIds
    .map((goal) => {
      const own = lines.filter((l) => l.goal_id === goal);
      return {
        goal_id: goal,
        calls: own.reduce((n, l) => n + l.calls, 0),
        cost_usd: sum(own),
        lines: own,
      };
    })
    .sort(
      (a, b) => b.cost_usd - a.cost_usd || Number(a.goal_id === null) - Number(b.goal_id === null),
    );
  const slots = [...new Set(lines.map((l) => l.slot))].sort().map((slot) => {
    const own = lines.filter((l) => l.slot === slot);
    const hosted = sum(own.filter((l) => l.cost_usd > 0));
    return { slot, cost_usd: sum(own), hosted_usd: hosted, if_local_saving_usd: hosted };
  });
  const providers = [...new Set(lines.map((l) => l.provider))]
    .map((provider) => {
      const own = lines.filter((l) => l.provider === provider);
      return { provider, calls: own.reduce((n, l) => n + l.calls, 0), cost_usd: sum(own) };
    })
    .sort((a, b) => b.cost_usd - a.cost_usd);
  return {
    as_of: '2026-01-01T00:00:00Z',
    total_usd: sum(lines),
    if_local_saving_usd: slots.reduce((n, s) => n + s.if_local_saving_usd, 0),
    goals,
    slots,
    providers,
  };
}

// Three goals' worth of spend: goal_1 on a hosted queen and a free local worker, goal_2 on a hosted
// worker, and one call that named no goal.
export const sampleLines = (): Schemas['CostLine'][] => [
  line({ goal_id: 'goal_1', slot: 'queen', provider: 'cloud', calls: 2, cost_usd: 0.03 }),
  line({
    goal_id: 'goal_1',
    slot: 'worker',
    provider: 'local',
    calls: 3,
    cost_usd: 0,
    input_tokens: 12000,
    output_tokens: 3400,
  }),
  line({ goal_id: 'goal_2', slot: 'worker', provider: 'cloud', calls: 1, cost_usd: 0.012 }),
  line({ goal_id: null, slot: 'queen', provider: 'cloud', calls: 1, cost_usd: 0.005 }),
];

export const sampleCost = (): Schemas['CostView'] => costFrom(sampleLines());

// An `llm.call` trail event; `over` overrides any member (a missing `usage` is `usage: null`).
export function callEvent(
  over: Partial<Schemas['TrailEventView']> = {},
): Schemas['TrailEventView'] {
  return {
    actor: 'queen',
    at: '2026-01-01T00:00:00Z',
    family: 'llm',
    id: 'ev_1',
    kind: 'llm.call',
    node_id: 'node_1',
    payload: {},
    provider: 'cloud',
    slot: 'queen',
    subject_id: 'queen',
    usage: {
      cached_tokens: 0,
      cost_usd: 0.02,
      input_tokens: 500,
      local: false,
      output_tokens: 100,
    },
    ...over,
  };
}

export const trailFrame = (event: Schemas['TrailEventView']): Schemas['TrailFrame'] => ({
  type: 'trail',
  event,
});

export interface Mounted {
  fake: FakeBrowser;
  store: Hive['store'];
}

export interface Reads {
  cost?: unknown;
  status?: number;
  body?: unknown;
}

export const costReads = (fake: FakeBrowser): number =>
  fake.requests.filter((r) => r.method === 'GET' && r.target === '/v1/cost').length;

// Renders the Cost screen on the fake browser; resolves once its stream socket exists and is open.
export async function mountCost(reads: Reads = {}): Promise<Mounted> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  fake.respond('GET /v1/cost', reads.status ?? 200, reads.body ?? reads.cost ?? sampleCost());
  const store = createHiveStore({ ...APP_SLICES, cost: costSlice });
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => undefined });
  await client.session.restore();
  render(
    createElement(HiveProvider, { hive: { store, client }, children: createElement(CostScreen) }),
  );
  await flush();
  fake.sockets.forEach((socket) => socket.open());
  await flush();
  return { fake, store };
}
