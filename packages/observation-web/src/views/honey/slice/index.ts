/**
 * The Honey slice's face: its state, its events, its reducer and the slice itself.
 *
 * The slice is a concept of several files (state, the folds, the reducer), so it is a package and
 * importers take everything from here. This is also where the slice declares itself into the
 * store's registry, so `useSlice('honey')` and `dispatch` are typed once `src/app.tsx` lists it.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/slice/`. Imported by the view's hooks and
 *   components, and by `src/app.tsx` for the table entry.
 *
 * Key invariants:
 *   - Re-exports and the registry merge only; no logic lives here.
 *
 * See Also:
 *   - src/store/registry.ts for the interface merged into below.
 */
import type { Slice } from '../../../store';
import { reduceHoney } from './reduce';
import { initialHoney } from './state';
import type { HoneyEvent, HoneyState } from './state';

export { cellOf, concerns } from './fold';
export { reduceHoney } from './reduce';
export { initialHoney, MAX_HITS, MAX_ITEMS } from './state';
export type {
  FolderEntry,
  HoneyEvent,
  HoneyFolder,
  HoneyState,
  ItemSummary,
  ItemView,
  ProvenanceView,
  ReadingState,
  SearchHit,
  SearchPage,
  SearchState,
  TrailEvent,
  TrailFrame,
} from './state';

export const honeySlice: Slice<HoneyState, HoneyEvent> = {
  initial: initialHoney,
  reduce: reduceHoney,
};

// Tells the store's hooks about this slice, so `useSlice('honey')` and `dispatch` are typed.
declare module '../../../store/registry' {
  interface SliceRegistry {
    honey: Slice<HoneyState, HoneyEvent>;
  }
}
