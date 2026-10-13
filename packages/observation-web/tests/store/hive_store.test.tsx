/**
 * Tests for `HiveStore` and its React bindings.
 *
 * The store must notify only when a slice really changed, keep unchanged slices identical, and
 * work with `useSyncExternalStore` so a component re-renders exactly when its slice does. The
 * component test mounts through the real provider with a client built on the fake browser.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/store/hive_store.ts and src/store/react.tsx.
 *
 * Key invariants:
 *   - Re-renders are counted, so "no extra render for another slice" is asserted, not hoped for.
 *
 * See Also:
 *   - docs/adr/0049-observation-hive-dashboard-stack.md (state and streams).
 */
import { act, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createLandingBoard } from '../../src/landing_board/client';
import { APP_SLICES } from '../../src/app';
import { createHiveStore, HiveProvider, summaryCounters, useSlice } from '../../src/store';
import { cellFrame } from '../support/frames';
import { createFakeBrowser } from '../support/fake_browser';

describe('HiveStore', () => {
  it('notifies listeners only when a slice changed', () => {
    const store = createHiveStore(APP_SLICES);
    let calls = 0;
    store.subscribe(() => (calls += 1));
    store.dispatch({ slice: 'summary', event: { type: 'cell', frame: cellFrame({ id: 'a' }) } });
    store.dispatch({ slice: 'summary', event: { type: 'cell', frame: cellFrame({ id: 'a' }) } });
    expect(calls).toBe(1);
  });

  it('keeps an untouched slice identical across other slices changing', () => {
    const store = createHiveStore(APP_SLICES);
    const before = store.getSnapshot().session;
    store.dispatch({ slice: 'connection', event: { type: 'reset', at: 1 } });
    expect(store.getSnapshot().session).toBe(before);
  });

  it('stops notifying after unsubscribe', () => {
    const store = createHiveStore(APP_SLICES);
    let calls = 0;
    const off = store.subscribe(() => (calls += 1));
    off();
    store.dispatch({ slice: 'connection', event: { type: 'reset', at: 1 } });
    expect(calls).toBe(0);
  });
});

describe('useSlice', () => {
  function Probe(props: { onRender: () => void }) {
    props.onRender();
    const counters = summaryCounters(useSlice('summary'));
    return <p>cells {counters ? counters.cells_active : 'none'}</p>;
  }

  it('re-renders a component when its slice changes, and not for another slice', () => {
    const store = createHiveStore(APP_SLICES);
    const client = createLandingBoard({
      browser: createFakeBrowser().browser,
      onSessionLost: () => {},
    });
    let renders = 0;
    render(
      <HiveProvider hive={{ store, client }}>
        <Probe onRender={() => (renders += 1)} />
      </HiveProvider>,
    );
    expect(screen.getByText('cells none')).toBeTruthy();
    act(() =>
      store.dispatch({ slice: 'summary', event: { type: 'cell', frame: cellFrame({ id: 'a' }) } }),
    );
    expect(screen.getByText('cells 1')).toBeTruthy();
    const afterSummary = renders;
    act(() => store.dispatch({ slice: 'connection', event: { type: 'reset', at: 1 } }));
    expect(renders).toBe(afterSummary);
  });

  it('throws outside a provider rather than rendering from nothing', () => {
    const quiet = console.error;
    console.error = () => {};
    try {
      expect(() => render(<Probe onRender={() => {}} />)).toThrow(/HiveProvider/);
    } finally {
      console.error = quiet;
    }
  });
});
