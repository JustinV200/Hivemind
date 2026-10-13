/**
 * The rates strip's model: one row of figures per risk tier, from the counts and audit rates.
 *
 * `by_tier_state` counts every proposal per tier and state, and `audit_rates` says how much of
 * each tier's finished work the judge looked at again and what it found. This module joins them
 * by tier: how many proposals, how many wait on the gate, how many it approved (a proposal that
 * passed its checks has been capped, applied, verified, or applied and then rolled back), how many
 * it refused, how many were rolled back, and the audit coverage and findings. The counts are the
 * server's, which can exceed the rows the queue lists.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/rates/`. Built by `rates_strip.tsx` from the
 *   slice; pure so the arithmetic is tested without rendering.
 *
 * Key invariants:
 *   - Tiers appear in the gate's order (least to most dangerous); a tier only in the audit rates
 *     still gets a row.
 *   - A fraction is null when the server's is (no finished work, no scored audit).
 *
 * See Also:
 *   - docs/observation/design.md section 5.9.
 */
import { tierRank } from '../slice';
import type { CappingState, ProposalState } from '../slice';

export interface TierRates {
  tier: string;
  proposals: number;
  waiting: number;
  approved: number;
  refused: number;
  rolledBack: number;
  finished: number;
  audited: number;
  findings: number;
  inconclusive: number;
  sampled: number | null;
  rejection: number | null;
}

const WAITING: ReadonlySet<ProposalState> = new Set(['PROPOSED', 'CHECKING']);
const APPROVED: ReadonlySet<ProposalState> = new Set([
  'CAPPED',
  'APPLIED',
  'VERIFIED',
  'ROLLED_BACK',
]);

export function tierRates(state: Pick<CappingState, 'counts' | 'rates'>): TierRates[] {
  const tiers = new Set([...state.counts.map((c) => c.tier), ...state.rates.map((r) => r.tier)]);
  const rows = [...tiers].map((tier): TierRates => {
    const mine = state.counts.filter((c) => c.tier === tier);
    const sum = (wanted: (s: ProposalState) => boolean): number =>
      mine.filter((c) => wanted(c.state)).reduce((total, c) => total + c.count, 0);
    const audit = state.rates.find((r) => r.tier === tier);
    return {
      tier,
      proposals: sum(() => true),
      waiting: sum((s) => WAITING.has(s)),
      approved: sum((s) => APPROVED.has(s)),
      refused: sum((s) => s === 'REJECTED'),
      rolledBack: sum((s) => s === 'ROLLED_BACK'),
      finished: audit?.finished ?? 0,
      audited: audit?.audited ?? 0,
      findings: audit?.rejected ?? 0,
      inconclusive: audit?.inconclusive ?? 0,
      sampled: audit?.sampled_fraction ?? null,
      rejection: audit?.rejection_fraction ?? null,
    };
  });
  return rows.sort((a, b) => tierRank(a.tier) - tierRank(b.tier) || (a.tier < b.tier ? -1 : 1));
}

// A fraction as a whole percent, or the hyphen the tables use for "none yet".
export function percent(fraction: number | null): string {
  return fraction === null ? '-' : `${Math.round(fraction * 100)}%`;
}
