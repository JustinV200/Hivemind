/**
 * The Capping slice's state, events and the small vocabulary the view shares.
 *
 * Capping is the gate every action passes: a bee proposes, the gate checks, and only a capped
 * proposal is applied. The slice holds what `GET /v1/capping` answers, flattened for the view:
 * one list of proposals (in flight, finished and rolled back joined by id), the gate's verdicts,
 * the sampled audits, the proposal counts per tier and state, and each tier's audit rates. A
 * proposal's tier is a `RiskTier` (`READ_ONLY` to `IRREVERSIBLE`), which is not a Comb Shield
 * level, so it is drawn as words and never with the Cell tier badge.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/slice/`. Read by the reducer, the hook and every
 *   table; the schema types are re-exported here so the view imports them from one place.
 *
 * Key invariants:
 *   - Stored records are the generated types as received; the slice adds no field of its own to a
 *     record, only the lists around them.
 *   - Every list is bounded (`MAX_PROPOSALS`, `MAX_VERDICTS`, `MAX_AUDITS`), newest first.
 *   - `ticks` counts `capping.*` trail frames since the view opened; the hook re-reads on a change.
 *
 * See Also:
 *   - docs/observation/design.md section 5.9.
 *   - hivemind/observation/api/capping.py, the fold this slice mirrors for live frames.
 */
import type { components } from '../../../landing_board/types';

type Schemas = components['schemas'];
export type CappingSnapshot = Schemas['CappingView'];
export type ProposalRow = Schemas['ProposalRow'];
export type VerdictRow = Schemas['VerdictRow'];
export type AuditRow = Schemas['AuditRow'];
export type TierStateCount = Schemas['TierStateCount'];
export type TierAuditRate = Schemas['TierAuditRate'];
export type ProposalState = Schemas['ProposalState'];
export type TrailFrame = Schemas['TrailFrame'];
export type TrailEvent = Schemas['TrailEventView'];

// The server keeps the newest 200 of each list; the slice holds the two proposal lists joined.
export const MAX_PROPOSALS = 400;
export const MAX_VERDICTS = 400;
export const MAX_AUDITS = 200;

// The gate's lifecycle in order, so chips and counts read the way a proposal moves.
export const STATES: readonly ProposalState[] = [
  'PROPOSED',
  'CHECKING',
  'CAPPED',
  'APPLIED',
  'VERIFIED',
  'REJECTED',
  'ROLLED_BACK',
];

// The risk tiers from least to most dangerous; a tier the table does not list sorts after these.
const TIERS: readonly string[] = [
  'READ_ONLY',
  'SCRATCH_WRITE',
  'OUTSIDE_SCRATCH_WRITE',
  'NETWORK_EGRESS',
  'SPEND',
  'DEVICE_COMMAND',
  'IRREVERSIBLE',
];

// A state with no way out: the proposal is finished.
const TERMINAL: ReadonlySet<ProposalState> = new Set(['VERIFIED', 'REJECTED', 'ROLLED_BACK']);

export const isTerminal = (state: ProposalState): boolean => TERMINAL.has(state);

export function tierRank(tier: string): number {
  const at = TIERS.indexOf(tier);
  return at < 0 ? TIERS.length : at;
}

export function stateRank(state: ProposalState): number {
  return STATES.indexOf(state);
}

export interface CappingState {
  // False until the first answer lands; frames only count (never fold) before it.
  seeded: boolean;
  proposals: readonly ProposalRow[];
  verdicts: readonly VerdictRow[];
  audits: readonly AuditRow[];
  counts: readonly TierStateCount[];
  rates: readonly TierAuditRate[];
  // The last refusal or failure of a read, shown through `ErrorNotice`; null when well.
  error: unknown;
  ticks: number;
}

export type CappingEvent =
  | { type: 'open' }
  | { type: 'seeded'; view: CappingSnapshot }
  | { type: 'failed'; error: unknown }
  | { type: 'trail'; frame: TrailFrame };

export const initialCapping: CappingState = {
  seeded: false,
  proposals: [],
  verdicts: [],
  audits: [],
  counts: [],
  rates: [],
  error: null,
  ticks: 0,
};
