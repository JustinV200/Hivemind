/**
 * `HiveStore`: the one in-memory store, built from a table of slices, readable by
 * `useSyncExternalStore`.
 *
 * There is no Redux or Zustand (ADR-0046). The store holds one slice per entry of the table it is
 * given, each folded by its own pure reducer from one event. A view adds a slice by adding an
 * entry to the table, never by editing this file. `subscribe` and `getSnapshot` are arrow
 * properties so they can be passed straight to `useSyncExternalStore`; a slice keeps its identity
 * when a reducer returns it unchanged, so a component that reads one slice does not re-render for
 * another's events.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Built once by `src/app.tsx`; written by `feed.ts`,
 *   the login screen, the shell and the views' hooks; read through `react.tsx`.
 *
 * Key invariants:
 *   - Listeners are told only when some slice actually changed.
 *   - The store never calls the network; frames reach it only through `dispatch`.
 *   - Each slice is bounded by its own reducer (see `slices/summary.ts`).
 *   - An unknown slice name is a type error, and at run time it changes nothing.
 *
 * See Also:
 *   - docs/adr/0046-observation-hive-dashboard-stack.md for the state and stream rules.
 *   - src/store/slice.ts for the slice shape, src/store/react.tsx for the hooks.
 */
import type { EventFor, SliceTable, StateTable } from './slice';

export class HiveStore<T extends SliceTable> {
  #state: StateTable<T>;
  readonly #listeners = new Set<() => void>();

  constructor(private readonly slices: T) {
    // The cast is the one place the table meets its derived state type: each member is the
    // slice's own `initial`, which `StateTable` types by key.
    this.#state = Object.fromEntries(
      Object.entries(slices).map(([name, slice]) => [name, slice.initial]),
    ) as StateTable<T>;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };

  getSnapshot = (): StateTable<T> => this.#state;

  dispatch = <K extends keyof T & string>(action: EventFor<T, K>): void => {
    const slice = this.slices[action.slice];
    // An unknown name cannot pass the type check; at run time it is ignored rather than thrown.
    if (!slice) return;
    const before = this.#state[action.slice];
    const after = slice.reduce(before, action.event);
    // A reducer that returns its input says "nothing changed": keep the identity and stay quiet.
    if (after === before) return;
    this.#state = { ...this.#state, [action.slice]: after } as StateTable<T>;
    for (const listener of this.#listeners) listener();
  };
}

export function createHiveStore<T extends SliceTable>(slices: T): HiveStore<T> {
  return new HiveStore(slices);
}
