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
 */
export { HiveStore } from './hive_store';
export type { HiveEvent, HiveState } from './hive_store';
export { HiveProvider, useHive, useSlice } from './react';
export type { Hive } from './react';
export { startFeeds } from './feed';
export { connectionWord, refusedStreams, staleSince } from './connection';
export type { ConnectionSlice, StreamName } from './connection';
export { summaryCounters } from './summary';
export type { SummaryCounters, SummarySeed, SummarySlice } from './summary';
export { SESSION_ENDED_NOTICE } from './session';
export type { Listener, SessionSlice } from './session';
