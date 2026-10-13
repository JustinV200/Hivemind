/**
 * The Cost slice's face: its state, its events, its reducer and the slice itself.
 *
 * The slice is a concept of several files (state, the fold, the reducer), so it is a package and
 * importers take everything from here. This is also where the slice declares itself into the
 * store's registry, so `useSlice('cost')` and `dispatch` are typed once `src/app.tsx` lists it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/cost/slice/`. Imported by the view's hook, tables and
 *   screen, and by `src/app.tsx` for the table entry.
 *
 * Key invariants:
 *   - Re-exports and the registry merge only; no logic lives here.
 *
 * See Also:
 *   - src/store/registry.ts for the interface merged into below.
 */
import type { Slice } from '../../../store';
import { reduceCost } from './reduce';
import { initialCost } from './state';
import type { CostEvent, CostState } from './state';

export { foldCall } from './fold';
export { reduceCost } from './reduce';
export { EMPTY_COST, initialCost, MAX_SEEN } from './state';
export type {
  CostEvent,
  CostLine,
  CostSnapshot,
  CostState,
  GoalCost,
  ProviderCost,
  SlotSaving,
  TrailEventView,
  TrailFrame,
} from './state';

export const costSlice: Slice<CostState, CostEvent> = {
  initial: initialCost,
  reduce: reduceCost,
};

// Tells the store's hooks about this slice, so `useSlice('cost')` and `dispatch` are typed.
declare module '../../../store/registry' {
  interface SliceRegistry {
    cost: Slice<CostState, CostEvent>;
  }
}
