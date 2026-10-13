/**
 * The store's face: what the rest of the app imports from `src/store/`.
 *
 * A concept that needs several files becomes a package with one face. Importers take the store,
 * its slices' types, the hooks and the feeds from here, so the file layout inside can change
 * without touching the shell, the router or the views.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Imported by `src/app.tsx`, `src/router/` and
 *   `src/shell/`.
 *
 * Key invariants:
 *   - Re-exports only; no logic lives here.
 *
 * See Also:
 *   - src/store/hive_store.ts for the store and src/store/react.tsx for the hooks.
 *   - src/store/registry.ts for how a view adds a slice.
 */
export { createHiveStore, HiveStore } from './hive_store';
export type { AppSlices, SliceRegistry } from './registry';
export type { EventFor, EventOf, Slice, SliceTable, StateOf, StateTable } from './slice';
export { HiveProvider, useHive, useSlice, useViewStream } from './react';
export type { Hive } from './react';
export { startFeeds } from './feed';
export { SHELL_SLICES } from './slices';
export { connectionWord, refusedStreams, staleSince } from './slices/connection';
export type { ConnectionSlice, StreamName } from './slices/connection';
export { summaryCounters } from './slices/summary';
export type { SummaryCounters, SummarySeed, SummarySlice } from './slices/summary';
export { SESSION_ENDED_NOTICE } from './slices/session';
export type { Listener, SessionSlice } from './slices/session';
