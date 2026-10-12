/**
 * Fold one `capping.*` trail event into the slice at once, the way the server would on a re-read.
 *
 * Every hop of a proposal is a trail event keyed by the proposal's id and acted by the proposing
 * bee. A hop moves the proposal to the state its kind names (and fills the failing check, the
 * rollback method or the action kind that hop carries); the gate's word after its checks
 * (`capped` or `rejected`) also adds a verdict, and `capping.audited` adds a sampled audit. The
 * payload holds ids, counts and reason codes only, so nothing here can show a reviewer's prose.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/slice/`. Called by `reduce.ts` for a seeded slice;
 *   mirrors `hivemind/observation/api/capping.py` so the live row equals the re-read one.
 *
 * Key invariants:
 *   - Pure and bounded: the lists are cut to their bounds, the newest kept.
 *   - A frame older than the proposal's newest event changes nothing, and a frame seen twice
 *     changes nothing the second time (a reconnect may replay one).
 *   - A proposal first seen on a later hop still gets a row, as on the server.
 *   - An unknown `capping.*` kind changes nothing.
 *
 * See Also:
 *   - src/views/capping/slice/counts.ts for the counts and rates.
 */
import { bumpState, noteAudit, noteFinished } from './counts';
import { isTerminal, MAX_AUDITS, MAX_PROPOSALS, MAX_VERDICTS } from './state';
import type {
  AuditRow,
  CappingState,
  ProposalRow,
  ProposalState,
  TrailEvent,
  VerdictRow,
} from './state';

// The capping.* kinds that move a proposal, and the state each reaches (the gate's own seven).
const KIND_STATE: Readonly<Record<string, ProposalState>> = {
  'capping.proposed': 'PROPOSED',
  'capping.checked': 'CHECKING',
  'capping.capped': 'CAPPED',
  'capping.applied': 'APPLIED',
  'capping.verified': 'VERIFIED',
  'capping.rejected': 'REJECTED',
  'capping.rolled_back': 'ROLLED_BACK',
};

const AUDITED = 'capping.audited';

const text = (value: unknown): string | null => (typeof value === 'string' ? value : null);

// Newest event first; equal times break on the id, so the order is total.
export function newestFirst(a: ProposalRow, b: ProposalRow): number {
  return Date.parse(b.updated_at) - Date.parse(a.updated_at) || (a.id < b.id ? 1 : -1);
}

function opened(event: TrailEvent, state: ProposalState): ProposalRow {
  return {
    id: event.subject_id,
    tier: text(event.payload['tier']) ?? '',
    state,
    bee_id: event.actor,
    task_id: text(event.payload['task_id']),
    cell_id: text(event.payload['cell_id']),
    proposed_at: event.at,
    updated_at: event.at,
    failing_check: null,
    rollback_method: null,
    action_kind: null,
  };
}

// The fields only some hops carry, read off the payload by the hop's kind.
function hopFields(event: TrailEvent): Partial<ProposalRow> {
  switch (event.kind) {
    case 'capping.rejected':
      return { failing_check: text(event.payload['failing_check']) };
    case 'capping.rolled_back':
      return { rollback_method: text(event.payload['method']) };
    case 'capping.applied':
      return { action_kind: text(event.payload['action_kind']) };
    default:
      return {};
  }
}

function advanced(held: ProposalRow, event: TrailEvent, state: ProposalState): ProposalRow {
  const tier = held.tier === '' ? (text(event.payload['tier']) ?? '') : held.tier;
  return { ...held, tier, state, updated_at: event.at, ...hopFields(event) };
}

// The gate's word on the proposal; the judge's silence is remembered from any earlier hop.
function verdictOf(
  row: ProposalRow,
  event: TrailEvent,
  earlier: readonly VerdictRow[],
): VerdictRow {
  const passed = event.payload['checks_passed'] ?? event.payload['checks_run'];
  const silent = earlier.some((v) => v.proposal_id === row.id && v.judge_error);
  return {
    proposal_id: row.id,
    tier: row.tier,
    verdict: event.kind === 'capping.capped' ? 'CAPPED' : 'REJECTED',
    failing_check: event.kind === 'capping.rejected' ? text(event.payload['failing_check']) : null,
    judge_error: silent || event.payload['judge_error'] === true,
    checks_run: typeof passed === 'number' ? passed : 0,
    bee_id: row.bee_id,
    task_id: row.task_id,
    at: event.at,
  };
}

function withVerdict(
  verdicts: readonly VerdictRow[],
  row: ProposalRow,
  event: TrailEvent,
): readonly VerdictRow[] {
  if (event.kind !== 'capping.capped' && event.kind !== 'capping.rejected') return verdicts;
  const verdict = verdictOf(row, event, verdicts);
  const seen = verdicts.some((v) => v.proposal_id === row.id && v.at === verdict.at);
  if (seen) return verdicts;
  const newest = [verdict, ...verdicts].sort(
    (a, b) => Date.parse(b.at) - Date.parse(a.at) || (a.proposal_id < b.proposal_id ? 1 : -1),
  );
  return newest.slice(0, MAX_VERDICTS);
}

function foldProposal(
  state: CappingState,
  event: TrailEvent,
  reached: ProposalState,
): CappingState {
  const held = state.proposals.find((p) => p.id === event.subject_id);
  // An older frame, or the same hop again, has nothing to add; a different hop at the very same
  // moment still moves the proposal on.
  const moment = Date.parse(event.at) - Date.parse(held?.updated_at ?? event.at);
  if (held && (moment < 0 || (moment === 0 && held.state === reached))) return state;
  const row = held ? advanced(held, event, reached) : opened(event, reached);
  const proposals = [row, ...state.proposals.filter((p) => p.id !== row.id)]
    .sort(newestFirst)
    .slice(0, MAX_PROPOSALS);
  // The proposal leaves its old (tier, state) count and joins the new one.
  const left = held ? bumpState(state.counts, held.tier, held.state, -1) : state.counts;
  const finishedNow = isTerminal(row.state) && !(held && isTerminal(held.state));
  return {
    ...state,
    proposals,
    verdicts: withVerdict(state.verdicts, row, event),
    counts: bumpState(left, row.tier, row.state, 1),
    rates: finishedNow ? noteFinished(state.rates, row.tier) : state.rates,
  };
}

function outcomeOf(value: unknown): AuditRow['outcome'] {
  return value === 'APPROVE' || value === 'REQUEST_CHANGES' || value === 'REJECT' ? value : null;
}

function foldAudit(state: CappingState, event: TrailEvent): CappingState {
  const proposal = state.proposals.find((p) => p.id === event.subject_id);
  // A replayed audit (same proposal, same moment) is already counted.
  if (state.audits.some((a) => a.proposal_id === event.subject_id && a.at === event.at)) {
    return state;
  }
  const audit: AuditRow = {
    proposal_id: event.subject_id,
    tier: text(event.payload['tier']) ?? proposal?.tier ?? '',
    outcome: outcomeOf(event.payload['outcome']),
    judge_error: event.payload['judge_error'] === true,
    bee_id: proposal?.bee_id ?? event.actor,
    task_id: proposal?.task_id ?? null,
    at: event.at,
  };
  const audits = [audit, ...state.audits]
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
    .slice(0, MAX_AUDITS);
  return { ...state, audits, rates: noteAudit(state.rates, audit) };
}

export function foldHop(state: CappingState, event: TrailEvent): CappingState {
  if (event.kind === AUDITED) return foldAudit(state, event);
  const reached = KIND_STATE[event.kind];
  return reached === undefined ? state : foldProposal(state, event, reached);
}
