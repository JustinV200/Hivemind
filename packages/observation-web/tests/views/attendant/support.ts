/**
 * Test builders and a harness for the Attendant view: a view, its parts, the frames, a mounted store.
 *
 * The generated types have many required members that a test about one node does not care about.
 * These builders fill them with plain defaults and let a test override only what it is about, so
 * each test reads as its point and still type-checks against the generated schema. `openHive`
 * makes the store (the app's table plus the Attendant slice, which `src/app.tsx` does not hold
 * until the orchestrator registers it) and a client on the fake browser, so a test renders the
 * screen directly.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by every test under tests/views/attendant/.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks these builders
 *     (and so every test) at compile time.
 *   - Defaults describe the Queen's view of one running task, `task_1`, placed on `cell_1` under
 *     `warden_1`, with no Alarms.
 *
 * See Also:
 *   - src/landing_board/types.ts, generated.
 *   - tests/support/fake_browser.ts for the fake the harness uses.
 */
import { APP_SLICES } from '../../../src/app';
import { createLandingBoard } from '../../../src/landing_board/client';
import type { components } from '../../../src/landing_board/types';
import { createHiveStore } from '../../../src/store';
import type { Hive } from '../../../src/store';
import { attendantSlice } from '../../../src/views/attendant';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import { attendantView, inboxEntry } from '../../support/frames';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];
export type Attendant = Schemas['AttendantView'];
export type Node = Schemas['TaskNode'];

// The shared builders for the whole view and one inbox entry, re-exported so tests import one place.
export { attendantView, inboxEntry };

export function taskNode(over: Partial<Node> = {}): Node {
  return {
    cell_id: 'cell_1',
    open_alarms: 0,
    role: 'DRONE',
    status: 'RUNNING',
    task_id: 'task_1',
    warden_id: 'warden_1',
    ...over,
  };
}

export function taskView(over: Partial<Schemas['TaskView']> = {}): Schemas['TaskView'] {
  return {
    attempt: 1,
    blocked_on: null,
    bound_tier: 'MEADOW',
    cell_id: 'cell_1',
    clearance: 'C1',
    created_at: '2026-01-01T00:00:00Z',
    depends_on: [],
    fraction_done: null,
    goal_id: 'goal_1',
    goal_request_id: null,
    id: 'task_1',
    isolation: 'PREFERRED',
    origin: 'QUEEN',
    outcome: null,
    requested_tier: 'MEADOW',
    spend_cap_usd: null,
    status: 'RUNNING',
    updated_at: '2026-01-01T00:00:00Z',
    warden_id: 'warden_1',
    ...over,
  };
}

export function graphFrame(
  principal: string,
  task: Partial<Schemas['TaskView']> = {},
): Schemas['TaskGraphFrame'] {
  return { type: 'task', principal, task: taskView(task) };
}

// A small graph: task_1 and task_2 feed task_3, and task_4 stands alone.
export function diamond(over: Partial<Attendant> = {}): Attendant {
  return attendantView({
    nodes: [
      taskNode({ task_id: 'task_1', status: 'SUCCEEDED' }),
      taskNode({ task_id: 'task_2', status: 'RUNNING', open_alarms: 2 }),
      taskNode({ task_id: 'task_3', status: 'PENDING', cell_id: null, warden_id: null }),
      taskNode({ task_id: 'task_4', status: 'BLOCKED', warden_id: null }),
    ],
    edges: [
      { before: 'task_1', after: 'task_3' },
      { before: 'task_2', after: 'task_3' },
    ],
    ...over,
  });
}

export interface OpenedHive {
  fake: FakeBrowser;
  hive: Hive;
  // Reads of the Attendant view the harness answered so far.
  reads: () => number;
}

// A signed-in client on the fake browser and a store holding the app's slices plus the Attendant's.
export async function openHive(
  answer: { status?: number; body?: unknown; principal?: string } = {},
): Promise<OpenedHive> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  const principal = answer.principal ?? 'queen';
  const path = `/v1/attendant/${principal}`;
  fake.respond(`GET ${path}`, answer.status ?? 200, answer.body ?? attendantView({ principal }));
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await client.session.restore();
  const store = createHiveStore({ ...APP_SLICES, attendant: attendantSlice });
  const reads = () => fake.requests.filter((r) => r.target === path).length;
  return { fake, hive: { store, client }, reads };
}
