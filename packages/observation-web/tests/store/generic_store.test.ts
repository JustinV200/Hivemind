/**
 * Tests for the generic store: a table of slices, typed state, typed dispatch.
 *
 * `createHiveStore` builds the store from whatever table it is given, so a view can add a slice
 * without editing the store. These tests use a small table of their own (a counter and a log) and
 * check the three promises: each slice starts from its `initial`, an event reaches only its own
 * reducer, and a name that is not in the table is refused by the compiler.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/store/hive_store.ts and src/store/slice.ts.
 *
 * Key invariants:
 *   - The `@ts-expect-error` lines are the tests of the types: if the store ever accepts an
 *     unknown slice or a wrong event, the directive is unused and the type check fails.
 *
 * See Also:
 *   - tests/store/hive_store.test.tsx for the app's table through React.
 */
import { describe, expect, it } from 'vitest';
import { createHiveStore, SHELL_SLICES } from '../../src/store';
import type { Slice } from '../../src/store';

type CounterEvent = { type: 'add'; by: number } | { type: 'noop' };
const counter: Slice<{ n: number }, CounterEvent> = {
  initial: { n: 0 },
  reduce: (state, event) => (event.type === 'add' ? { n: state.n + event.by } : state),
};

type LogEvent = { type: 'line'; text: string };
const log: Slice<readonly string[], LogEvent> = {
  initial: [],
  reduce: (state, event) => [...state, event.text],
};

const table = { counter, log };

describe('createHiveStore', () => {
  it('starts every slice from its initial state, keyed by name', () => {
    const store = createHiveStore(table);
    expect(store.getSnapshot()).toEqual({ counter: { n: 0 }, log: [] });
    expect(store.getSnapshot().counter).toBe(counter.initial);
  });

  it('folds an event into its own slice only, leaving the others identical', () => {
    const store = createHiveStore(table);
    const before = store.getSnapshot().log;
    store.dispatch({ slice: 'counter', event: { type: 'add', by: 2 } });
    expect(store.getSnapshot().counter.n).toBe(2);
    expect(store.getSnapshot().log).toBe(before);
  });

  it('stays quiet when a reducer returns its input', () => {
    const store = createHiveStore(table);
    let calls = 0;
    store.subscribe(() => (calls += 1));
    store.dispatch({ slice: 'counter', event: { type: 'noop' } });
    expect(calls).toBe(0);
    store.dispatch({ slice: 'log', event: { type: 'line', text: 'a' } });
    expect(calls).toBe(1);
  });

  it('types dispatch per slice and refuses an unknown slice', () => {
    const store = createHiveStore(table);
    // @ts-expect-error 'nope' is not a slice in this table.
    store.dispatch({ slice: 'nope', event: { type: 'noop' } });
    // @ts-expect-error a log event is not a counter event.
    store.dispatch({ slice: 'counter', event: { type: 'line', text: 'x' } });
    // @ts-expect-error the state has no slice 'nope' either.
    void store.getSnapshot().nope;
    // At run time the unknown name changed nothing and threw nothing.
    expect(store.getSnapshot()).toEqual({ counter: { n: 0 }, log: [] });
  });

  it('takes the shell slices as a table another slice can be added to', () => {
    const store = createHiveStore({ ...SHELL_SLICES, counter });
    store.dispatch({ slice: 'counter', event: { type: 'add', by: 1 } });
    expect(store.getSnapshot().counter.n).toBe(1);
    expect(store.getSnapshot().session.status).toBe('restoring');
  });
});
