/**
 * The Forage slice's face: its state, its events, its reducer and the slice itself.
 *
 * The slice is a concept of several files (state, reducer, request list), so it is a package and
 * importers take everything from here. This is also where the slice declares itself into the
 * store's registry, so `useSlice('forage')` and `dispatch` are typed once `src/app.tsx` lists it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/forage/slice/`. Imported by the view's hook, tables and
 *   diagrams, and by `src/app.tsx` for the table entry.
 *
 * Key invariants:
 *   - Re-exports and the registry merge only; no logic lives here.
 *
 * See Also:
 *   - src/store/registry.ts for the interface merged into below.
 */
import type { Slice } from '../../../store';
import { initialForage, type ForageEvent, type ForageState } from './state';
import { reduceForage } from './reduce';

export { initialForage, MAX_GONE, MAX_REQUESTS } from './state';
export type {
  CapacityView,
  Decision,
  ForageEvent,
  ForageFrame,
  ForageSnapshot,
  ForageState,
  GrantView,
  HeadroomView,
  LlmView,
  ProviderView,
  RequestRecord,
  ReserveView,
  SlotView,
  TrailEventView,
  WardenList,
  WardenView,
} from './state';
export { byHolderThenId, reduceForage } from './reduce';
export { foldRequests } from './requests';

export const forageSlice: Slice<ForageState, ForageEvent> = {
  initial: initialForage,
  reduce: reduceForage,
};

// Tells the store's hooks about this slice, so `useSlice('forage')` and `dispatch` are typed.
declare module '../../../store/registry' {
  interface SliceRegistry {
    forage: Slice<ForageState, ForageEvent>;
  }
}
