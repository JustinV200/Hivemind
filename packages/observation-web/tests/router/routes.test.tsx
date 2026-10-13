/**
 * Tests for the route table, the resolver and the `useRoute` hook.
 *
 * Every documented route must resolve to its own `Route`, an unknown hash must be "not found",
 * the loopback-only screen must resolve only for a loopback session, and a route must survive a
 * round trip through its href. The hook is tested through a component that follows the hash.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/router/.
 *
 * Key invariants:
 *   - The table here is written out by hand from design.md section 5, not derived from the code.
 *
 * See Also:
 *   - docs/observation/design.md section 5 and ADR-0049 (routing, loopback-only screens).
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { createLandingBoard } from '../../src/landing_board/client';
import { hrefFor, resolveRoute, useRoute } from '../../src/router';
import type { Route } from '../../src/router';
import { APP_SLICES } from '../../src/app';
import { createHiveStore, HiveProvider } from '../../src/store';
import { createFakeBrowser } from '../support/fake_browser';

const TABLE: [string, Route][] = [
  ['', { name: 'fleet' }],
  ['#/', { name: 'fleet' }],
  ['#/fleet', { name: 'fleet' }],
  ['#/cells/cell_1', { name: 'cell', id: 'cell_1' }],
  ['#/forage', { name: 'forage' }],
  ['#/thoughts', { name: 'thoughts', bee: 'queen' }],
  ['#/thoughts/queen', { name: 'thoughts', bee: 'queen' }],
  ['#/thoughts/worker_9', { name: 'thoughts', bee: 'worker_9' }],
  ['#/attendant', { name: 'attendant', principal: 'queen' }],
  ['#/attendant/warden_2', { name: 'attendant', principal: 'warden_2' }],
  ['#/chat', { name: 'chat' }],
  ['#/honey', { name: 'honey', path: '' }],
  ['#/honey/cells/cell_1', { name: 'honey', path: 'cells/cell_1' }],
  ['#/cost', { name: 'cost' }],
  ['#/capping', { name: 'capping' }],
  ['#/devices', { name: 'devices' }],
  ['#/enrol', { name: 'enrol' }],
];

describe('resolveRoute', () => {
  it.each(TABLE)('resolves %s on the Hive Stand', (hash, route) => {
    expect(resolveRoute(hash, 'loopback')).toEqual(route);
  });

  it.each(['#/nope', '#/cells', '#/cells/a/b', '#/fleet/x', '#/chat/x', '#/%E0%A4%A'])(
    'treats %s as not found',
    (hash) => {
      expect(resolveRoute(hash, 'loopback')).toEqual({ name: 'not_found' });
    },
  );

  it('hides the loopback-only screen off the Hive Stand and before login', () => {
    expect(resolveRoute('#/enrol', 'remote')).toEqual({ name: 'not_found' });
    expect(resolveRoute('#/enrol', null)).toEqual({ name: 'not_found' });
    // Everything else is the same on a remote listener.
    expect(resolveRoute('#/devices', 'remote')).toEqual({ name: 'devices' });
  });

  it('decodes a percent-encoded parameter once', () => {
    expect(resolveRoute('#/cells/a%2Fb', 'remote')).toEqual({ name: 'cell', id: 'a/b' });
  });

  it('round-trips every resolvable route through its href', () => {
    for (const [, route] of TABLE) {
      expect(resolveRoute(hrefFor(route), 'loopback')).toEqual(route);
    }
  });
});

describe('useRoute', () => {
  afterEach(() => {
    window.location.hash = '';
  });

  function Where() {
    return <p>{JSON.stringify(useRoute())}</p>;
  }

  function mount(listener: 'loopback' | 'remote') {
    const store = createHiveStore(APP_SLICES);
    store.dispatch({
      slice: 'session',
      event: {
        type: 'opened',
        session: { deviceId: 'd', listener, expiresAt: 'x', needsStepUp: false },
      },
    });
    const client = createLandingBoard({
      browser: createFakeBrowser().browser,
      onSessionLost: () => {},
    });
    render(
      <HiveProvider hive={{ store, client }}>
        <Where />
      </HiveProvider>,
    );
  }

  it('follows the hash as it changes', async () => {
    window.location.hash = '#/forage';
    mount('remote');
    expect(screen.getByText('{"name":"forage"}')).toBeTruthy();
    // jsdom fires hashchange on a later turn, so let it arrive inside act.
    await act(async () => {
      window.location.hash = '#/cells/cell_7';
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByText('{"name":"cell","id":"cell_7"}')).toBeTruthy();
  });

  it('answers #/enrol with not found on a remote session and with the screen on loopback', () => {
    window.location.hash = '#/enrol';
    mount('remote');
    expect(screen.getByText('{"name":"not_found"}')).toBeTruthy();
  });

  it('resolves #/enrol on a loopback session', () => {
    window.location.hash = '#/enrol';
    mount('loopback');
    expect(screen.getByText('{"name":"enrol"}')).toBeTruthy();
  });
});
