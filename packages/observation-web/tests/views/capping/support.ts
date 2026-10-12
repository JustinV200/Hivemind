/**
 * Test builders and a harness for the Capping view: rows, the read model, trail frames, a hive.
 *
 * The generated types have many required members that a test about one filter does not care about.
 * These builders fill them with plain defaults and let a test override only what it is about, so
 * each test reads as its point and still type-checks against the generated schema. `openHive` makes
 * the store (the app's table plus the Capping slice, which `src/app.tsx` does not hold until the
 * orchestrator registers it) and a client on the fake browser, so a test renders the view directly.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by every test under tests/views/capping/.
 *
 * Key invariants:
 *   - Return types are the generated schema types, so a contract change breaks these builders
 *     (and so every test) at compile time.
 *   - Defaults describe one proposal, `prop_1`, proposed by `worker_1` for `task_1` on `cell_1`.
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
import { cappingSlice } from '../../../src/views/capping/slice';
import { createFakeBrowser } from '../../support/fake_browser';
import type { FakeBrowser } from '../../support/fake_browser';
import { storeSession } from '../../support/session_fixture';

type Schemas = components['schemas'];
export type Snapshot = Schemas['CappingView'];
export type Proposal = Schemas['ProposalRow'];

export function proposalRow(over: Partial<Proposal> = {}): Proposal {
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

export function auditRow(over: Partial<Schemas['AuditRow']> = {}): Schemas['AuditRow'] {
  return {
    at: '2026-01-01T00:05:00Z',
    bee_id: 'worker_1',
    judge_error: false,
    outcome: 'APPROVE',
    proposal_id: 'prop_1',
    task_id: 'task_1',
    tier: 'SCRATCH_WRITE',
    ...over,
  };
}

export function rateRow(over: Partial<Schemas['TierAuditRate']> = {}): Schemas['TierAuditRate'] {
  return {
    audited: 0,
    finished: 0,
    inconclusive: 0,
    rejected: 0,
    rejection_fraction: null,
    sampled_fraction: null,
    tier: 'SCRATCH_WRITE',
    ...over,
  };
}

export function countRow(
  tier: string,
  state: Proposal['state'],
  count: number,
): Schemas['TierStateCount'] {
  return { tier, state, count };
}

// A read model with nothing in it; a test fills the lists it is about.
export function emptyView(over: Partial<Snapshot> = {}): Snapshot {
  return {
    audit_rates: [],
    audits: [],
    by_tier_state: [],
    finished: [],
    in_flight: [],
    rollbacks: [],
    verdicts: [],
    ...over,
  };
}

const waitingRow = (): Proposal =>
  proposalRow({ id: 'prop_wait', state: 'CHECKING', updated_at: '2026-01-01T00:00:10Z' });

const rejectedRow = (): Proposal =>
  proposalRow({
    id: 'prop_rej',
    tier: 'NETWORK_EGRESS',
    state: 'REJECTED',
    bee_id: 'worker_2',
    cell_id: null,
    failing_check: 'allowlist',
    updated_at: '2026-01-01T00:00:20Z',
  });

// An Exoskeleton action (kind GUI) that was applied and then rolled back by snapshot.
const rolledBackRow = (): Proposal =>
  proposalRow({
    id: 'prop_roll',
    tier: 'IRREVERSIBLE',
    state: 'ROLLED_BACK',
    bee_id: 'worker_3',
    cell_id: 'cell_2',
    action_kind: 'GUI',
    rollback_method: 'SNAPSHOT',
    updated_at: '2026-01-01T00:00:30Z',
  });

// Three proposals in the three kinds of place: waiting, rejected and rolled back.
export function busyView(): Snapshot {
  return emptyView({
    in_flight: [waitingRow()],
    finished: [rolledBackRow(), rejectedRow()],
    rollbacks: [rolledBackRow()],
    verdicts: [
      verdictRow({
        proposal_id: 'prop_roll',
        tier: 'IRREVERSIBLE',
        bee_id: 'worker_3',
        checks_run: 4,
      }),
      verdictRow({
        proposal_id: 'prop_rej',
        tier: 'NETWORK_EGRESS',
        bee_id: 'worker_2',
        verdict: 'REJECTED',
        failing_check: 'allowlist',
        checks_run: 1,
        at: '2026-01-01T00:00:19Z',
      }),
    ],
    by_tier_state: [
      countRow('SCRATCH_WRITE', 'CHECKING', 1),
      countRow('NETWORK_EGRESS', 'REJECTED', 1),
      countRow('IRREVERSIBLE', 'ROLLED_BACK', 1),
    ],
    audits: [
      auditRow({
        proposal_id: 'prop_roll',
        tier: 'IRREVERSIBLE',
        outcome: 'REJECT',
        bee_id: 'worker_3',
      }),
    ],
    audit_rates: [
      rateRow({
        tier: 'IRREVERSIBLE',
        finished: 1,
        audited: 1,
        rejected: 1,
        sampled_fraction: 1,
        rejection_fraction: 1,
      }),
    ],
  });
}

// One capping.* event as the trail stream carries it; the kind and the subject are what a test sets.
export function hop(
  kind: string,
  subject: string,
  over: Partial<Schemas['TrailEventView']> = {},
): Schemas['TrailFrame'] {
  return {
    type: 'trail',
    event: {
      actor: 'worker_1',
      at: '2026-01-01T00:01:00Z',
      family: kind.split('.')[0] ?? '',
      id: `evt_${kind}_${subject}`,
      kind,
      node_id: 'queen',
      payload: {},
      subject_id: subject,
      ...over,
    },
  };
}

export interface OpenedHive {
  fake: FakeBrowser;
  hive: Hive;
  // Reads of the queue the harness answered so far.
  reads: () => number;
}

// A signed-in client on the fake browser and a store holding the app's slices plus Capping's.
export async function openHive(
  answer: { status?: number; body?: unknown } = {},
): Promise<OpenedHive> {
  const fake = createFakeBrowser();
  await storeSession(fake.browser);
  fake.respond('GET /v1/capping', answer.status ?? 200, answer.body ?? busyView());
  const client = createLandingBoard({ browser: fake.browser, onSessionLost: () => {} });
  await client.session.restore();
  const store = createHiveStore({ ...APP_SLICES, capping: cappingSlice });
  const reads = () => fake.requests.filter((r) => r.target === '/v1/capping').length;
  return { fake, hive: { store, client }, reads };
}
