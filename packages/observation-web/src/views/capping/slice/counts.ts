/**
 * Keep the per-tier proposal counts and audit rates right as live frames move proposals.
 *
 * The read answers with counts over every proposal the Hive knows, which can be more than the rows
 * it lists, so the view's strip must not be recounted from rows. A frame that moves a proposal from
 * one state to another therefore adjusts the counts by one each way, a proposal reaching a finished
 * state adds to its tier's `finished`, and a sampled audit adds to its tier's audit figures. The
 * fractions are recomputed with the same formulas the server uses. The next re-read replaces all of
 * it, so any drift is bounded to one second of frames.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/slice/`. Called by `fold.ts` only.
 *
 * Key invariants:
 *   - Pure: lists in, new lists out; the inputs are never changed.
 *   - A count that reaches zero leaves the list, as the server omits empty pairs.
 *   - `sampled_fraction` is audited / finished and `rejection_fraction` is rejected / scored, each
 *     null when its divisor is zero.
 *
 * See Also:
 *   - hivemind/observation/api/capping.py (`_counts`, `_rates`) for the formulas.
 */
import type { AuditRow, ProposalState, TierAuditRate, TierStateCount } from './state';

// Moves the (tier, state) count by `delta`, adding the pair on first sight and dropping it at zero.
export function bumpState(
  counts: readonly TierStateCount[],
  tier: string,
  state: ProposalState,
  delta: number,
): TierStateCount[] {
  const found = counts.some((c) => c.tier === tier && c.state === state);
  const moved = found
    ? counts.map((c) =>
        c.tier === tier && c.state === state ? { ...c, count: c.count + delta } : c,
      )
    : [...counts, { tier, state, count: delta }];
  return moved.filter((c) => c.count > 0);
}

function blankRate(tier: string): TierAuditRate {
  return {
    tier,
    finished: 0,
    audited: 0,
    rejected: 0,
    inconclusive: 0,
    sampled_fraction: null,
    rejection_fraction: null,
  };
}

// The two fractions, from the counts, by the server's formulas.
function withFractions(rate: TierAuditRate): TierAuditRate {
  const scored = rate.audited - rate.inconclusive;
  return {
    ...rate,
    sampled_fraction: rate.finished > 0 ? rate.audited / rate.finished : null,
    rejection_fraction: scored > 0 ? rate.rejected / scored : null,
  };
}

// Applies `change` to the tier's rate row (made on first sight) and recomputes its fractions.
function adjusted(
  rates: readonly TierAuditRate[],
  tier: string,
  change: (rate: TierAuditRate) => TierAuditRate,
): TierAuditRate[] {
  const known = rates.some((r) => r.tier === tier);
  const base = known ? rates : [...rates, blankRate(tier)];
  return base.map((r) => (r.tier === tier ? withFractions(change(r)) : r));
}

// One more proposal of this tier reached a finished state.
export function noteFinished(rates: readonly TierAuditRate[], tier: string): TierAuditRate[] {
  return adjusted(rates, tier, (r) => ({ ...r, finished: r.finished + 1 }));
}

// One more sampled audit: counted, and as a finding when the judge rejected, or unscored when it
// never answered.
export function noteAudit(rates: readonly TierAuditRate[], audit: AuditRow): TierAuditRate[] {
  return adjusted(rates, audit.tier, (r) => ({
    ...r,
    audited: r.audited + 1,
    rejected: r.rejected + (audit.outcome === 'REJECT' ? 1 : 0),
    inconclusive: r.inconclusive + (audit.outcome === null ? 1 : 0),
  }));
}
