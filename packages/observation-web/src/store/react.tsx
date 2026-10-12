/**
 * React bindings for the store: a provider, `useHive` and `useSlice`.
 *
 * Views read live state through `useSyncExternalStore`, which re-renders a component only when
 * the slice it reads changes identity. The provider also carries the Landing Board client, so
 * the login screen and the shell reach both without prop drilling and tests can hand in fakes.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Mounted by `src/app.tsx`; used by the router, the
 *   shell, the login screen and every view.
 *
 * Key invariants:
 *   - `useSlice` returns the slice object itself, never a fresh copy, so the snapshot is stable
 *     between changes (a requirement of `useSyncExternalStore`).
 *   - Using a hook outside the provider throws at once rather than rendering from nothing.
 *
 * See Also:
 *   - src/store/hive_store.ts for the store these hooks read.
 */
import { createContext, useContext, useSyncExternalStore } from 'react';
import type { ReactElement, ReactNode } from 'react';
import type { LandingBoard } from '../landing_board/client';
import type { HiveState, HiveStore } from './hive_store';

export interface Hive {
  store: HiveStore;
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

export function useSlice<K extends keyof HiveState>(key: K): HiveState[K] {
  const { store } = useHive();
  return useSyncExternalStore(store.subscribe, () => store.getSnapshot()[key]);
}
