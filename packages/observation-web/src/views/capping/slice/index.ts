/**
 * The Capping slice's face: its state, its events, its reducer and the slice itself.
 *
 * The slice is a concept of several files (state, counts, fold, reducer), so it is a package and
 * importers take everything from here. This is also where the slice declares itself into the
 * store's registry, so `useSlice('capping')` and `dispatch` are typed once `src/app.tsx` lists it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/capping/slice/`. Imported by the view's hook, tables and
 *   strip, and by `src/app.tsx` for the table entry.
 *
 * Key invariants:
 *   - Re-exports and the registry merge only; no logic lives here.
 *
 * See Also:
 *   - src/store/registry.ts for the interface merged into below.
 */
import type { Slice } from '../../../store';
import { reduceCapping } from './reduce';
import { initialCapping } from './state';
import type { CappingEvent, CappingState } from './state';

export {
  initialCapping,
  isTerminal,
  MAX_AUDITS,
  MAX_PROPOSALS,
  MAX_VERDICTS,
  stateRank,
  STATES,
  tierRank,
  wordsOf,
} from './state';
export type {
  AuditRow,
  CappingEvent,
  CappingSnapshot,
  CappingState,
  ProposalRow,
  ProposalState,
  TierAuditRate,
  TierStateCount,
  TrailEvent,
  TrailFrame,
  VerdictRow,
} from './state';
export { reduceCapping } from './reduce';

export const cappingSlice: Slice<CappingState, CappingEvent> = {
  initial: initialCapping,
  reduce: reduceCapping,
};

// Tells the store's hooks about this slice, so `useSlice('capping')` and `dispatch` are typed.
declare module '../../../store/registry' {
  interface SliceRegistry {
    capping: Slice<CappingState, CappingEvent>;
  }
}
