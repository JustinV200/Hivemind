/**
 * Test builders and a harness for the Cell page: a full page, its parts, the frames, a mounted view.
 *
 * The generated types have many required members that a test about one panel does not care about.
 * These builders fill them with plain defaults and let a test override only what it is about, so
 * each test reads as its point and still type-checks against the generated schema. `openHive` makes
 * the store (the app's table plus the Cell slice, which `src/app.tsx` does not hold until the
 * orchestrator registers it) and a client on the fake browser, so a test renders the view directly.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by every test under tests/views/cell/.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks these builders
 *     (and so every test) at compile time.
 *   - Defaults describe one healthy Real Cell, `cell_1`, whose Warden `warden_1` runs on the Cell
 *     with one Drone, `worker_1`, working on `task_1`.
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
import { cellSlice } from '../../../src/views/cell/slice';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];
export type CellPage = Schemas['CellPageView'];
type CellRecord = Schemas['CellView'];

export function cellRecord(over: Partial<CellRecord> = {}): CellRecord {
  return {
    access_level: 'SCRATCH',
    can_host_model: false,
    comb_shield: 'MEADOW',
    current_tasks: ['task_1'],
    has_browser: false,
    has_display: false,
    id: 'cell_1',
    kind: 'REAL',
    last_event: null,
    last_event_at: null,
    lease_state: 'OPEN',
    mask_state: null,
    mode: 'ACTIVE',
    name: 'laptop',
    os: 'LINUX',
    source: 'hive_stand',
    virtual_status: null,
    warden_id: 'warden_1',
    ...over,
  };
}

export function beeRow(over: Partial<Schemas['BeeRow']> = {}): Schemas['BeeRow'] {
  return {
    id: 'worker_1',
    role: 'DRONE',
    status: 'RUNNING',
    task_id: 'task_1',
    context_tokens_used: 32000,
    context_window: 128000,
    context_fraction: 0.25,
    spend_usd: 0.5,
    ...over,
  };
}

export function wardenRow(over: Partial<Schemas['BeeRow']> = {}): Schemas['BeeRow'] {
  return beeRow({ id: 'warden_1', role: 'WARDEN', status: 'ACTIVE', task_id: null, ...over });
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

export function grantView(over: Partial<Schemas['GrantView']> = {}): Schemas['GrantView'] {
  return {
    cell_id: 'cell_1',
    expires_at: '2026-01-01T01:00:00Z',
    holder: 'warden_1',
    id: 'grant_1',
    max_sub_bees: 4,
    revision: 2,
    seats: 3,
    spend_budget: 10,
    spent: 2,
    state: 'ACTIVE',
    task_id: null,
    token_budget: 100000,
    tokens_spent: 40000,
    ...over,
  };
}

type Forage = NonNullable<CellPage['forage']>;

export function forageView(over: Partial<Forage> = {}): Forage {
  return {
    grants: [grantView()],
    pending_requests: [],
    spend_budget_usd: 10,
    spent_usd: 2,
    token_budget: 100000,
    tokens_spent: 40000,
    ...over,
  };
}

export function waxEntry(over: Partial<Schemas['WaxEntryView']> = {}): Schemas['WaxEntryView'] {
  return {
    expires_at: '2026-01-02T00:00:00Z',
    id: 'wax_1',
    origin: 'BEE',
    proposed_at: '2026-01-01T00:00:00Z',
    proposer: 'worker_1',
    severity: 'CAUTION',
    state: 'WRITTEN',
    task_id: null,
    ...over,
  };
}

export function alarmView(over: Partial<Schemas['OpenAlarmView']> = {}): Schemas['OpenAlarmView'] {
  return {
    cell_id: 'cell_1',
    escalated: false,
    id: 'alarm_1',
    kind: 'WORKER_CRASHED',
    raised_at: '2026-01-01T00:00:00Z',
    severity: 'WARNING',
    task_id: null,
    ...over,
  };
}

export function proposalRow(over: Partial<Schemas['ProposalRow']> = {}): Schemas['ProposalRow'] {
  return {
    action_kind: null,
    bee_id: 'worker_1',
    cell_id: 'cell_1',
    failing_check: null,
    id: 'prop_1',
    proposed_at: '2026-01-01T00:00:00Z',
    rollback_method: null,
    state: 'CHECKING',
    task_id: 'task_1',
    tier: 'SCRATCH_WRITE',
    updated_at: '2026-01-01T00:00:01Z',
    ...over,
  };
}

export function verdictRow(over: Partial<Schemas['VerdictRow']> = {}): Schemas['VerdictRow'] {
  return {
    at: '2026-01-01T00:00:02Z',
    bee_id: 'worker_1',
    checks_run: 3,
    failing_check: null,
    judge_error: false,
    proposal_id: 'prop_1',
    task_id: 'task_1',
    tier: 'SCRATCH_WRITE',
    verdict: 'CAPPED',
    ...over,
  };
}

export function pageView(over: Partial<CellPage> = {}): CellPage {
  return {
    alarms: [],
    as_of: '2026-01-01T00:00:00Z',
    bees: [wardenRow(), beeRow()],
    capping: {
      audit_rates: [],
      audits: [],
      by_tier_state: [],
      finished: [],
      in_flight: [],
      rollbacks: [],
      verdicts: [],
    },
    cell: cellRecord(),
    exoskeleton_attached: null,
    forage: null,
    honey_folder: '/cells/cell_1',
    hosting: {
      models_from: 'hybrid',
      plan_revision: 3,
      slots: [{ slot: 'WORKER', primary_source: 'local-qwen', models_from: 'local' }],
    },
    last_patrol_at: null,
    lease_age_s: 3900,
    status: 'active',
    tasks: [],
    thought_principals: ['warden_1', 'worker_1'],
    warden: {
      cell_id: 'cell_1',
      id: 'warden_1',
      last_heartbeat_at: null,
      live_grants: 1,
      missed_heartbeats: 0,
      offline: false,
      state: 'ACTIVE',
      sub_bees: 1,
    },
    warden_runs: 'on_cell',
    wax: [],
    ...over,
  };
}

export function cellFrameOf(over: Partial<CellRecord> = {}): Schemas['CellFrame'] {
  return { type: 'cell', cell: cellRecord(over) };
}

export function sampleFrame(
  over: Partial<Schemas['TelemetrySampleView']> = {},
): Schemas['TelemetryFrame'] {
  return {
    type: 'telemetry',
    sample: {
      at: '2026-01-01T00:00:05Z',
      blockers: [],
      context_window: 128000,
      goal: 'edit the file',
      last_actions: [],
      spend: 0.75,
      sub_bees: null,
      task_id: 'task_1',
      tokens_used: 64000,
      warden_id: 'warden_1',
      warden_state: 'ACTIVE',
      worker_id: 'worker_1',
      worker_state: 'RUNNING',
      ...over,
    },
  };
}

export function trailFrame(over: Partial<Schemas['TrailEventView']> = {}): Schemas['TrailFrame'] {
  return {
    type: 'trail',
    event: {
      actor: 'system',
      at: '2026-01-01T00:00:06Z',
      family: 'alarm',
      id: 'evt_1',
      kind: 'alarm.raised',
      node_id: 'queen',
      payload: {},
      subject_id: 'cell_1',
      ...over,
    },
  };
}

export interface OpenedHive {
  fake: FakeBrowser;
  hive: Hive;
  // Reads of the page the harness answered so far.
  pageReads: () => number;
}

// A signed-in client on the fake browser and a store holding the app's slices plus the Cell's.
export async function openHive(
  answer: { status?: number; body?: unknown; id?: string } = {},
): Promise<OpenedHive> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  const id = answer.id ?? 'cell_1';
  fake.respond(`GET /v1/cells/${id}/page`, answer.status ?? 200, answer.body ?? pageView());
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await client.session.restore();
  const store = createHiveStore({ ...APP_SLICES, cell: cellSlice });
  const pageReads = () => fake.requests.filter((r) => r.target === `/v1/cells/${id}/page`).length;
  return { fake, hive: { store, client }, pageReads };
}
