/**
 * Tests for the whole app shell: login, strip, nav, routes, logout and session end.
 *
 * These mount the real `App` with the fake browser, so the composition root, store, client,
 * feeds, router and views are all exercised together. They check what a person sees: the login
 * screen when there is no session, the summary strip with seeded and then live counters, the
 * strip going stale when a stream drops, the nav with the current item marked and Enrol only on
 * the Hive Stand, the theme toggle, a push notice surfacing in any view, and the screen a session
 * end or a log out leaves behind.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/app.tsx, src/shell/, src/views/login/ and the
 *   placeholder views.
 *
 * Key invariants:
 *   - Sockets are driven by the test (open, frames, close codes); no real network or timer runs.
 *
 * See Also:
 *   - docs/observation/design.md sections 2, 4 and 5.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import App from '../../src/app';
import { loadSession } from '../../src/landing_board/client/keys';
import { createFakeBrowser, flush } from '../support/fake_browser';
import type { FakeBrowser } from '../support/fake_browser';
import { cellFrame, SEED, telemetryFrame } from '../support/frames';
import { storeSession } from '../support/session_fixture';

afterEach(() => {
  window.location.hash = '';
});

async function openShell(
  options: { listener?: string; needsStepUp?: boolean; seed?: boolean; keepHash?: boolean } = {},
) {
  const fake = createFakeBrowser();
  await storeSession(fake.browser, {
    listener: options.listener ?? 'remote',
    needsStepUp: options.needsStepUp ?? false,
  });
  fake.respond('GET /v1/devices/me', 200, { name: 'Pixel 9' });
  if (options.seed !== false) fake.respond('GET /v1/summary', 200, SEED);
  // The shell is under test, not a view: unless a test chose a route, an unknown one keeps the
  // outlet on "not found", so the only sockets and reads are the shell's own (the default route
  // would open the Fleet's too).
  if (!window.location.hash && !options.keepHash) window.location.hash = '#/nowhere';
  render(<App browser={fake.browser} />);
  // With the hash kept empty the default route's view opens its own stream beside the shell's.
  await waitFor(() => expect(fake.sockets.length).toBeGreaterThanOrEqual(3));
  // The seed is a signed request, so wait for its answer before a test relies on its numbers.
  if (options.seed !== false) await screen.findByRole('group', { name: 'Bees: 5 active' });
  return fake;
}

const live = (fake: FakeBrowser) =>
  act(async () => {
    fake.sockets.forEach((socket) => socket.open());
    await flush();
  });

describe('login screen', () => {
  it('shows when there is no session, with no nav and no strip', async () => {
    const fake = createFakeBrowser();
    render(<App browser={fake.browser} />);
    expect(
      await screen.findByRole('heading', { name: 'Log in to the Observation Hive' }),
    ).toBeTruthy();
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('logs in through the passkey ceremony and shows the shell', async () => {
    const fake = createFakeBrowser();
    fake.respond('POST /v1/auth/challenge', 200, {
      device_id: 'dev_1',
      nonce: 'n',
      expires_at: 'x',
      passkey_options: { challenge: 'abc' },
    });
    fake.respond('POST /v1/auth/login', 201, {
      device_id: 'dev_1',
      token: 'tok',
      listener: 'remote',
      expires_at: '2030-01-01T00:00:00Z',
      needs_step_up: false,
    });
    render(<App browser={fake.browser} />);
    fireEvent.change(await screen.findByLabelText('Device'), { target: { value: 'dev_1' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy();
    expect(fake.passkeyOptions).toHaveLength(1);
    expect((await loadSession(fake.store))?.token).toBe('tok');
  });

  it('shows a refusal with its code and sentence and clears the password', async () => {
    const fake = createFakeBrowser();
    fake.respond('POST /v1/auth/challenge', 401, {
      error: 'hivemind.entrance.authentication_failed',
      detail: 'Authentication failed.',
    });
    render(<App browser={fake.browser} />);
    fireEvent.change(await screen.findByLabelText('Device'), { target: { value: 'dev_1' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    const alert = await screen.findByRole('alert');
    expect(alert.dataset.code).toBe('hivemind.entrance.authentication_failed');
    expect(alert.textContent).toContain('The Hive did not accept this login.');
    expect((screen.getByLabelText('Password') as HTMLInputElement).value).toBe('');
  });

  it('prefills the device the last login used', async () => {
    const fake = createFakeBrowser();
    await fake.store.put('device', 'dev_remembered');
    render(<App browser={fake.browser} />);
    await waitFor(() =>
      expect((screen.getByLabelText('Device') as HTMLInputElement).value).toBe('dev_remembered'),
    );
  });
});

describe('summary strip', () => {
  it('shows the seeded counters at once and is a quiet status region', async () => {
    await openShell();
    const bees = await screen.findByRole('group', { name: 'Bees: 5 active' });
    expect(within(bees).getByText('5')).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Cells: 3 active, 2 inactive' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Kind: 1 Real, 4 Virtual' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Thinking: 2 LLM, 3 autopilot' })).toBeTruthy();
    const strip = screen.getAllByRole('status').find((el) => el.classList.contains('strip'))!;
    expect(strip.getAttribute('aria-live')).toBe('off');
  });

  it('shows dashes while nothing has arrived', async () => {
    await openShell({ seed: false });
    expect(screen.getByRole('group', { name: 'Bees: loading' })).toBeTruthy();
  });

  it('updates live from the streams, matching them once they cover the seed', async () => {
    const fake = await openShell();
    await live(fake);
    act(() => {
      for (const id of ['a', 'b', 'c', 'd', 'e']) {
        fake.sockets[0]!.receive(
          cellFrame({
            id,
            kind: 'REAL',
            mode: id < 'c' ? 'ACTIVE' : 'WATCH',
            virtual_status: null,
          }),
        );
      }
      for (const id of ['k1', 'k2']) {
        fake.sockets[1]!.receive(telemetryFrame({ worker_id: id, worker_state: 'RUNNING' }));
      }
    });
    expect(screen.getByRole('group', { name: 'Cells: 2 active, 3 inactive' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Kind: 5 Real, 0 Virtual' })).toBeTruthy();
    // Two bees streamed of the five the seed counted: the seed still stands.
    expect(screen.getByRole('group', { name: 'Bees: 5 active' })).toBeTruthy();
  });

  it('reads connecting, never stale, while quiet streams wait out the hello deadline, then live', async () => {
    const fake = await openShell();
    await live(fake);
    // Sockets are open and silent: nothing has dropped, so the bar is not "reconnecting" and the
    // strip (seeded by the summary read) is not stale.
    expect(screen.getByText('connecting')).toBeTruthy();
    expect(screen.queryByText('reconnecting')).toBeNull();
    expect(screen.queryByText(/stale since/)).toBeNull();
    act(() => fake.advance(5000));
    expect(screen.getByText('live')).toBeTruthy();
    expect(screen.queryByText(/stale since/)).toBeNull();
  });

  it('dims and says stale since when a stream drops, and the bar says reconnecting', async () => {
    const fake = await openShell();
    await live(fake);
    // The Entrance accepted every hello: the deadline passed with the sockets still open.
    act(() => fake.advance(5000));
    expect(screen.getByText('live')).toBeTruthy();
    act(() => fake.sockets[0]!.serverClose(1006));
    expect(screen.getByText(/stale since \d\d:\d\d:\d\d/)).toBeTruthy();
    expect(screen.getByText('reconnecting')).toBeTruthy();
    const strip = screen.getAllByRole('status').find((el) => el.classList.contains('strip'))!;
    expect(strip.className).toContain('strip--stale');
  });

  it('says no access for the groups a refused stream feeds', async () => {
    const fake = await openShell();
    act(() => fake.sockets[1]!.serverClose(4403));
    expect(screen.getByRole('group', { name: 'Bees: no access' })).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Cells: 3 active, 2 inactive' })).toBeTruthy();
  });
});

describe('top bar and nav', () => {
  it('shows the device name and the listener', async () => {
    await openShell({ listener: 'remote' });
    expect(await screen.findByText('Pixel 9')).toBeTruthy();
    expect(screen.getByText('remote')).toBeTruthy();
  });

  it('lists the nav items, marks the current one, and keeps Enrol off a remote session', async () => {
    window.location.hash = '#/forage';
    await openShell({ listener: 'remote' });
    const nav = screen.getByRole('navigation', { name: 'Main' });
    for (const label of [
      'Fleet',
      'Forage',
      'Thoughts',
      'Attendant',
      'Chat',
      'Honey',
      'Cost',
      'Capping',
      'Devices',
    ]) {
      expect(within(nav).getByRole('link', { name: label })).toBeTruthy();
    }
    expect(within(nav).queryByRole('link', { name: 'Enrol' })).toBeNull();
    expect(within(nav).getByRole('link', { name: 'Forage' }).getAttribute('aria-current')).toBe(
      'page',
    );
    expect(
      within(nav).getByRole('link', { name: 'Fleet' }).getAttribute('aria-current'),
    ).toBeNull();
  });

  it('adds Enrol on the Hive Stand', async () => {
    await openShell({ listener: 'loopback' });
    expect(
      within(screen.getByRole('navigation')).getByRole('link', { name: 'Enrol' }),
    ).toBeTruthy();
  });

  it('opens and closes the More group on a phone', async () => {
    await openShell();
    const more = screen.getByRole('button', { name: 'More' });
    expect(more.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(more);
    expect(more.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('navigation').className).toContain('nav--more-open');
  });

  it('shows the step-up banner only while the session needs one', async () => {
    await openShell({ needsStepUp: true });
    expect(screen.getByText(/new network/)).toBeTruthy();
  });

  it('has no banner otherwise', async () => {
    await openShell();
    expect(screen.queryByText(/new network/)).toBeNull();
  });

  it("tells the stylesheet the head's measured height, so the rail sits under a notice", async () => {
    // jsdom has no ResizeObserver: a fake records the callback and the test fires it by hand.
    let observed: (() => void) | null = null;
    class FakeResizeObserver {
      constructor(callback: () => void) {
        observed = callback;
      }
      observe(): void {}
      disconnect(): void {
        observed = null;
      }
    }
    Object.assign(globalThis, { ResizeObserver: FakeResizeObserver });
    try {
      await openShell();
      const head = screen.getByRole('banner');
      Object.defineProperty(head, 'offsetHeight', { value: 162 });
      act(() => observed?.());
      const shell = document.querySelector<HTMLElement>('.shell')!;
      expect(shell.style.getPropertyValue('--head-h')).toBe('162px');
    } finally {
      Reflect.deleteProperty(globalThis, 'ResizeObserver');
    }
  });
});

describe('theme and notices', () => {
  it('switches the theme on <html> and remembers it', async () => {
    delete document.documentElement.dataset.theme;
    window.localStorage.clear();
    await openShell();
    const toggle = screen.getByRole('button', { name: /^Theme: Light/ });
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem('hive.theme')).toBe('dark');
    expect(screen.getByRole('button', { name: /^Theme: Dark/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /^Theme: Dark/ }));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('surfaces a push notice in any view, links to the chat, and clears it there', async () => {
    const fake = await openShell();
    await live(fake);
    const push = fake.sockets.find((socket) => socket.url.endsWith('/v1/push/stream'))!;
    act(() => {
      push.receive({
        event_id: 'event_1',
        kind: 'question_waiting',
        ref: 'question_1',
        created_at: '2026-01-01T00:00:00Z',
      });
    });
    expect(screen.getByText('A question is waiting for you.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Answer in the chat' }).getAttribute('href')).toBe(
      '#/chat',
    );
    // The chat shows the question itself, so opening it takes the notice off the bar.
    await act(async () => {
      window.location.hash = '#/chat';
      await flush();
    });
    expect(screen.queryByText('A question is waiting for you.')).toBeNull();
  });
});

describe('routes', () => {
  it('draws the view for a route, with its parameter', async () => {
    window.location.hash = '#/cells/cell_9';
    await openShell();
    // The Cell page's own reads are unscripted here; its header still names the route's Cell.
    expect(screen.getByRole('heading', { name: /Cell/ })).toBeTruthy();
    expect(screen.getAllByText('cell_9').length).toBeGreaterThan(0);
  });

  it('draws the Fleet on an empty hash and Not found for an unknown route', async () => {
    await openShell({ keepHash: true });
    expect(screen.getByRole('heading', { name: 'Fleet' })).toBeTruthy();
    await act(async () => {
      window.location.hash = '#/nowhere';
      await flush();
    });
    expect(screen.getByRole('heading', { name: 'Not found' })).toBeTruthy();
  });

  it('answers #/enrol with Not found off the Hive Stand, and the screen on it', async () => {
    window.location.hash = '#/enrol';
    await openShell({ listener: 'remote' });
    expect(screen.getByRole('heading', { name: 'Not found' })).toBeTruthy();
  });

  it('shows the Enrol placeholder on loopback', async () => {
    window.location.hash = '#/enrol';
    await openShell({ listener: 'loopback' });
    expect(screen.getByRole('heading', { name: 'Enrol' })).toBeTruthy();
  });

  it('skips to the main area without changing the route', async () => {
    window.location.hash = '#/forage';
    await openShell();
    fireEvent.click(screen.getByText('Skip to content'));
    expect(document.activeElement?.id).toBe('main');
    expect(window.location.hash).toBe('#/forage');
  });
});

describe('leaving', () => {
  it('logs out: ends the session, deletes the key and shows the login screen', async () => {
    const fake = await openShell();
    fake.respond('POST /v1/auth/logout', 204);
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    expect(
      await screen.findByRole('heading', { name: 'Log in to the Observation Hive' }),
    ).toBeTruthy();
    expect(await loadSession(fake.store)).toBeNull();
    // Every stream was closed with the shell.
    expect(fake.sockets.every((socket) => socket.closedWith === 1000)).toBe(true);
  });

  it('drops to login with a notice when a stream says the session ended (4410)', async () => {
    const fake = await openShell();
    await live(fake);
    act(() => fake.sockets[0]!.serverClose(4410));
    expect(
      await screen.findByRole('heading', { name: 'Log in to the Observation Hive' }),
    ).toBeTruthy();
    expect(screen.getByText('Your session ended. Log in again.')).toBeTruthy();
    // The dead key and token are deleted, so a reload cannot restore the finished session.
    await waitFor(async () => expect(await loadSession(fake.store)).toBeNull());
  });

  it('drops to login when a signed request is answered 401', async () => {
    const fake = createFakeBrowser();
    await storeSession(fake.browser);
    fake.respond('GET /v1/summary', 401, {
      error: 'hivemind.entrance.authentication_failed',
      detail: 'no',
    });
    render(<App browser={fake.browser} />);
    expect(await screen.findByText('Your session ended. Log in again.')).toBeTruthy();
    expect(await loadSession(fake.store)).toBeNull();
  });
});
