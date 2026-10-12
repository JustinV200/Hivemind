/**
 * `SliceRegistry`: every slice the running app holds, by name, as one type the hooks read.
 *
 * `useSlice('session')` must know that the answer is a `SessionSlice`, and the hooks live in
 * `src/store/` below the views, so they cannot import a view's types. The registry is the seam:
 * it lists the built-in slices here, and a view adds its own by merging into this interface from
 * its `slice.ts` (see `src/views/thoughts/slice.ts`). `APP_SLICES` in `src/app.tsx` is annotated
 * with the merged interface, so a slice declared here and left out of the table is a type error.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Read by `react.tsx`, `feed.ts` and `hive_store.ts`;
 *   merged into by each view that owns a slice.
 *
 * Key invariants:
 *   - Types only: nothing here exists at run time.
 *   - A view merges with `declare module '<path to>/store/registry' { interface SliceRegistry {
 *     name: Slice<State, Event> } }`, naming the module that declares the interface, not the face.
 *
 * See Also:
 *   - src/store/slice.ts for `Slice`, src/app.tsx for the table.
 */
import type { Slice } from './slice';
import type { ConnectionEvent, ConnectionSlice } from './slices/connection';
import type { SessionEvent, SessionSlice } from './slices/session';
import type { SummaryEvent, SummarySlice } from './slices/summary';

export interface SliceRegistry {
  session: Slice<SessionSlice, SessionEvent>;
  connection: Slice<ConnectionSlice, ConnectionEvent>;
  summary: Slice<SummarySlice, SummaryEvent>;
}

// The registry as a table type (a mapped type, so it satisfies `SliceTable`'s index signature).
export type AppSlices = { [K in keyof SliceRegistry]: SliceRegistry[K] };
