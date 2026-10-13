/**
 * The Capping slice's reducer: seed from the read, count and fold what the trail stream brings.
 *
 * The view seeds the slice from `GET /v1/capping`. A `capping.*` trail frame folds at once (the
 * proposal's new state, a verdict, a rollback, an audit) and also bumps `ticks`, which is the
 * hook's cue to read again once, for what a frame does not carry (the true counts past the row
 * bound, rates). A frame that arrives before the first answer only bumps `ticks`: there is nothing
 * to fold into yet and the answer will replace it. A trail frame of another family changes nothing.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/slice/`. Wrapped into `cappingSlice` by the
 *   package face and run by the store; no clock and no request here.
 *
 * Key invariants:
 *   - Pure: the same state and event always give the same state.
 *   - A reducer that changes nothing returns the state it was given, so nothing re-renders.
 *   - The answer is authoritative: it replaces every list, whatever frames folded since the read.
 *
 * See Also:
 *   - src/views/capping/slice/fold.ts for the frame fold, src/views/capping/use_capping.ts.
 */
import { foldHop, newestFirst } from './fold';
import { MAX_AUDITS, MAX_PROPOSALS, MAX_VERDICTS } from './state';
import type { CappingEvent, CappingSnapshot, CappingState, ProposalRow, TrailFrame } from './state';

// The three row lists of the answer joined by id (they overlap), newest first, cut to the bound.
function joined(view: CappingSnapshot): ProposalRow[] {
  const byId = new Map<string, ProposalRow>();
  for (const row of [...view.in_flight, ...view.finished, ...view.rollbacks]) {
    if (!byId.has(row.id)) byId.set(row.id, row);
  }
  return [...byId.values()].sort(newestFirst).slice(0, MAX_PROPOSALS);
}

function seeded(state: CappingState, view: CappingSnapshot): CappingState {
  return {
    ...state,
    seeded: true,
    proposals: joined(view),
    verdicts: view.verdicts.slice(0, MAX_VERDICTS),
    audits: view.audits.slice(0, MAX_AUDITS),
    counts: view.by_tier_state,
    rates: view.audit_rates,
    error: null,
  };
}

function trailed(state: CappingState, frame: TrailFrame): CappingState {
  const { event } = frame;
  // Only the capping family concerns this view.
  if (!event.kind.startsWith('capping.')) return state;
  const folded = state.seeded ? foldHop(state, event) : state;
  return { ...folded, ticks: state.ticks + 1 };
}

export function reduceCapping(state: CappingState, event: CappingEvent): CappingState {
  switch (event.type) {
    case 'open':
      // Coming back keeps the rows but forgets frames counted on an earlier visit, so no read
      // is owed for them (the mount's own read is the seed).
      return state.ticks === 0 && state.error === null
        ? state
        : { ...state, ticks: 0, error: null };
    case 'seeded':
      return seeded(state, event.view);
    case 'failed':
      return { ...state, error: event.error };
    case 'trail':
      return trailed(state, event.frame);
  }
}
