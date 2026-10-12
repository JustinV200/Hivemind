/**
 * `Slice<S, E>`: the shape of one concern in the store, and the types derived from a table of them.
 *
 * A slice is an initial state and a pure reducer from the state and one event. The store is built
 * from a table of slices keyed by name; everything else (the state's shape, the events a
 * `dispatch` accepts) is read off that table by the types here, so a view adds a slice by adding a
 * table entry and never by editing the store.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/store/`. Used by `hive_store.ts`, `registry.ts`, the built-in
 *   slices in `slices/` and every view's `slice.ts`.
 *
 * Key invariants:
 *   - A reducer returns the state it was given when an event changes nothing, so the store can
 *     tell "no change" by identity and a component that reads another slice is not re-rendered.
 *   - Reducers are pure: no clock, no I/O, no randomness; times and ids come in with the event.
 *
 * See Also:
 *   - src/store/hive_store.ts for the store, src/store/registry.ts for how views add slices.
 */

// `reduce` is a method (not a function-typed property) on purpose: method parameters are checked
// bivariantly, which is what lets `Slice<Mine, MyEvent>` sit in a `SliceTable` whose entries are
// `Slice<unknown, unknown>` without a cast or an `any`.
export interface Slice<S, E> {
  readonly initial: S;
  reduce(state: S, event: E): S;
}

export type SliceTable = Readonly<Record<string, Slice<unknown, unknown>>>;

export type StateOf<L> = L extends Slice<infer S, never> ? S : never;
export type EventOf<L> = L extends { reduce(state: never, event: infer E): unknown } ? E : never;

// The whole state: one member per slice in the table, typed by that slice.
export type StateTable<T extends SliceTable> = { readonly [K in keyof T]: StateOf<T[K]> };

// What `dispatch` accepts: a slice's name with an event that slice's reducer understands.
export type EventFor<T extends SliceTable, K extends keyof T> = {
  slice: K;
  event: EventOf<T[K]>;
};
