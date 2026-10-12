/**
 * React bindings for the store: a provider, `useHive`, `useSlice` and `useViewStream`.
 *
 * Views read live state through `useSyncExternalStore`, which re-renders a component only when
 * the slice it reads changes identity. The provider also carries the Landing Board client, so
 * the login screen and the shell reach both without prop drilling and tests can hand in fakes.
 * `useViewStream` is how a view keeps itself live: it opens one Entrance stream for as long as the
 * view is mounted, so no view ever calls the client's `openStream` itself.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Mounted by `src/app.tsx`; used by the router, the
 *   shell, the login screen and every view.
 *
 * Key invariants:
 *   - `useSlice` returns the slice object itself, never a fresh copy, so the snapshot is stable
 *     between changes (a requirement of `useSyncExternalStore`).
 *   - Using a hook outside the provider throws at once rather than rendering from nothing.
 *   - `useViewStream` opens on mount and closes on unmount; it reopens only if its path, slice,
 *     `toEvent` or `resume` change identity, so pass module-level functions, not inline ones.
 *
 * See Also:
 *   - src/store/hive_store.ts for the store these hooks read.
 */
import { createContext, useContext, useEffect, useSyncExternalStore } from 'react';
import type { ReactElement, ReactNode } from 'react';
import type { LandingBoard, StreamOptions } from '../landing_board/client';
import type { HiveStore } from './hive_store';
import type { AppSlices } from './registry';
import type { EventOf, StateOf } from './slice';
import { openViewStream } from './view_stream';

export interface Hive {
  store: HiveStore<AppSlices>;
  client: LandingBoard;
}

const HiveContext = createContext<Hive | null>(null);

export function HiveProvider(props: { hive: Hive; children: ReactNode }): ReactElement {
  return <HiveContext.Provider value={props.hive}>{props.children}</HiveContext.Provider>;
}

export function useHive(): Hive {
  const hive = useContext(HiveContext);
  if (!hive) throw new Error('useHive must be used inside a HiveProvider');
  return hive;
}

export function useSlice<K extends keyof AppSlices>(name: K): StateOf<AppSlices[K]> {
  const { store } = useHive();
  return useSyncExternalStore(store.subscribe, () => store.getSnapshot()[name]);
}

// Folds one Entrance stream into a slice while the calling view is mounted, and reports the
// stream's state to the connection slice the top bar shows. `resume` is for a stream whose frames
// carry a cursor (the client resumes from it on a 4409 close).
export function useViewStream<K extends keyof AppSlices & string, F>(
  path: string,
  slice: K,
  toEvent: (frame: F) => EventOf<AppSlices[K]>,
  resume?: StreamOptions<F>['resume'],
): void {
  const { client, store } = useHive();
  useEffect(
    () => openViewStream({ client, store, nowMs: () => Date.now(), path, slice, toEvent, resume }),
    [client, store, path, slice, toEvent, resume],
  );
}
