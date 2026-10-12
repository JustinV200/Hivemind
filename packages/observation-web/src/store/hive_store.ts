/**
 * `HiveStore`: the one in-memory store, one slice per concern, readable by `useSyncExternalStore`.
 *
 * There is no Redux or Zustand (ADR-0046). The store holds a slice for the session, for the
 * connection and for the summary, each folded by its own pure reducer from one tagged event.
 * `subscribe` and `getSnapshot` are arrow properties so they can be passed straight to
 * `useSyncExternalStore`; a slice keeps its identity when a reducer returns it unchanged, so a
 * component that reads one slice does not re-render for another's events.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Built once by `src/app.tsx`; written by `feed.ts`,
 *   the login screen and the shell; read through `react.tsx`.
 *
 * Key invariants:
 *   - Listeners are told only when some slice actually changed.
 *   - The store never calls the network; frames reach it only through `dispatch`.
 *   - Each slice is bounded by its own reducer (see `summary.ts`).
 *
 * See Also:
 *   - docs/adr/0046-observation-hive-dashboard-stack.md for the state and stream rules.
 *   - src/store/react.tsx for the hooks.
 */
import { initialConnection, reduceConnection } from './connection';
import type { ConnectionEvent, ConnectionSlice } from './connection';
import { initialSession, reduceSession } from './session';
import type { SessionEvent, SessionSlice } from './session';
import { initialSummary, reduceSummary } from './summary';
import type { SummaryEvent, SummarySlice } from './summary';

export interface HiveState {
  session: SessionSlice;
  connection: ConnectionSlice;
  summary: SummarySlice;
}

export type HiveEvent =
  | { slice: 'session'; event: SessionEvent }
  | { slice: 'connection'; event: ConnectionEvent }
  | { slice: 'summary'; event: SummaryEvent };

export const initialHiveState: HiveState = {
  session: initialSession,
  connection: initialConnection,
  summary: initialSummary,
};

function fold(state: HiveState, action: HiveEvent): HiveState {
  switch (action.slice) {
    case 'session': {
      const session = reduceSession(state.session, action.event);
      return session === state.session ? state : { ...state, session };
    }
    case 'connection': {
      const connection = reduceConnection(state.connection, action.event);
      return connection === state.connection ? state : { ...state, connection };
    }
    case 'summary': {
      const summary = reduceSummary(state.summary, action.event);
      return summary === state.summary ? state : { ...state, summary };
    }
  }
}

export class HiveStore {
  #state: HiveState = initialHiveState;
  readonly #listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => {
      this.#listeners.delete(listener);
    };
  };

  getSnapshot = (): HiveState => this.#state;

  dispatch = (action: HiveEvent): void => {
    const next = fold(this.#state, action);
    if (next === this.#state) return;
    this.#state = next;
    for (const listener of this.#listeners) listener();
  };
}
