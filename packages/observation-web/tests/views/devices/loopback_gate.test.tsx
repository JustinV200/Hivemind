/**
 * The loopback gate: every loopback-only operation in the document is reachable only on the Hive Stand.
 *
 * Codingrules 8.15 and design.md 5.10 say approval, unlock, revoke, capability changes, invites and
 * reopening exist on the loopback listener only. The Entrance enforces it (the remote listener
 * answers a 404, which the Python route test proves); the Observation Hive adds three layers of its
 * own, and this test checks them from the document. It lists every operation marked
 * `x-hive-listeners: ["loopback"]`, requires each to be either mapped to a control or listed as
 * deliberately without one, then mounts the Devices and Enrol screens on a remote session (none of
 * the controls may exist, the Enrol screen is "Not found" and sends nothing) and on a loopback
 * session (every mapped control exists). Last, it reads the view sources and requires that each
 * loopback-only path is written only in the two files that hold the gated writes.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/devices/, src/views/enrol/ and the
 *   document docs/entrance/openapi.json, like tests/shell/write_routes.test.ts does for writes.
 *
 * Key invariants:
 *   - A new loopback-only operation in the document fails this test until a person decides how, or
 *     whether, the UI reaches it.
 *   - Operations open to both listeners (lock, reduce) are checked to be absent where the design
 *     says the UI keeps them on the Hive Stand.
 *
 * See Also:
 *   - tests/shell/write_routes.test.ts, tests/router/routes.test.tsx for the other layers.
 */
/// <reference types="vite/client" />
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import document from '../../../../../docs/entrance/openapi.json';
import { HiveProvider } from '../../../src/store';
import { DevicesScreen } from '../../../src/views/devices';
import { EnrolScreen } from '../../../src/views/enrol';
import { flush } from '../../support/fake_browser';
import { deviceView, modeView, openHive } from './support';
import type { OpenedHive } from './support';

type Operation = { 'x-hive-listeners'?: string[] };

// Every operation the document marks as served on the loopback listener alone.
function loopbackOnly(): string[] {
  const found: string[] = [];
  for (const [template, item] of Object.entries(document.paths)) {
    for (const [method, operation] of Object.entries(item as Record<string, Operation>)) {
      const listeners = operation['x-hive-listeners'];
      if (listeners?.length === 1 && listeners[0] === 'loopback') {
        found.push(`${method.toUpperCase()} ${template}`);
      }
    }
  }
  return found.sort();
}

// How the UI reaches each one: the accessible name of its control, or null when it has none.
const CONTROLS: Record<string, RegExp | null> = {
  'POST /v1/devices/{device_id}/capabilities': /^Edit capabilities of /,
  'POST /v1/devices/{device_id}/revoke': /^Revoke /,
  'POST /v1/devices/{device_id}/unlock': /^Unlock /,
  'POST /v1/entrance/invites': /^Mint invite$/,
  'DELETE /v1/entrance/invites/{device_id}': /^Withdraw invite$/,
  'POST /v1/entrance/open': /^Reopen the Entrance$/,
  'POST /v1/entrance/pending/{device_id}/approve': /^Approve /,
  'POST /v1/entrance/pending/{device_id}/deny': /^Deny /,
  // Added from the command line (`hive entrance operator add`, a program's own registration).
  'POST /v1/entrance/operators': null,
  'POST /v1/entrance/register': null,
};

// Open to both listeners in the document, but this UI keeps them on the Hive Stand.
const KEPT_ON_THE_STAND = /^Lock /;

const waitingDevice = () =>
  deviceView({
    id: 'dev_wait',
    name: 'phone',
    status: 'PENDING',
    description: { name: 'Tablet', platform: 'iPadOS', user_agent: '' },
  });

const stand = (): Promise<OpenedHive> =>
  openHive({
    listener: 'loopback',
    devices: [
      deviceView({ id: 'dev_test', name: 'Console', loopback_bound: true }),
      deviceView(),
      deviceView({ id: 'dev_old', name: 'Old tablet', status: 'LOCKED' }),
    ],
    mode: modeView({ mode: 'REDUCED', remote_listening: false }),
    pending: [waitingDevice()],
  });

async function mountBoth(opened: OpenedHive, sockets: number): Promise<void> {
  render(
    <HiveProvider hive={opened.hive}>
      <DevicesScreen />
      <EnrolScreen />
    </HiveProvider>,
  );
  await waitFor(() => expect(opened.fake.sockets).toHaveLength(sockets));
  await act(async () => {
    opened.fake.sockets.forEach((socket) => socket.open());
    await flush();
  });
  await screen.findByText('Pixel 9');
}

const present = (name: RegExp): boolean => screen.queryAllByRole('button', { name }).length > 0;

describe('the document', () => {
  it('has exactly the loopback-only operations this test knows how to reach', () => {
    expect(loopbackOnly()).toEqual(Object.keys(CONTROLS).sort());
  });
});

describe('a remote session', () => {
  it('has none of the controls, and the Enrol screen is Not found and silent', async () => {
    const opened = await openHive({
      listener: 'remote',
      devices: [deviceView(), deviceView({ id: 'dev_old', name: 'Old tablet', status: 'LOCKED' })],
      mode: modeView({ mode: 'REDUCED', remote_listening: false }),
      pending: [waitingDevice()],
    });
    await mountBoth(opened, 1);
    for (const control of [...Object.values(CONTROLS), KEPT_ON_THE_STAND]) {
      if (control) expect(present(control), String(control)).toBe(false);
    }
    expect(screen.getByRole('heading', { name: 'Not found' })).toBeTruthy();
    // Nothing the Enrol screen reads or writes was sent; only the Devices screen's own reads were.
    expect(opened.paths().filter((p) => p.includes('/pending') || p.includes('/invites'))).toEqual(
      [],
    );
    expect(opened.fake.requests.filter((r) => r.method !== 'GET')).toEqual([]);
  });
});

describe('the Hive Stand', () => {
  it("has every control the document's loopback-only operations are mapped to", async () => {
    const opened = await stand();
    await mountBoth(opened, 2);
    fireEvent.change(screen.getByLabelText('What do you call the device'), {
      target: { value: 'p' },
    });
    opened.fake.respond('POST /v1/entrance/invites', 201, {
      code: 'ABCD-EFGH-IJKL',
      device_id: 'dev_invited',
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      qr_svg: '<svg/>',
      url: 'https://hive.test/enrol#code=ABCD-EFGH-IJKL',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Mint invite' }));
    await screen.findByRole('button', { name: 'Withdraw invite' });
    await waitFor(() => expect(present(/^Approve /)).toBe(true));
    for (const [operation, control] of Object.entries(CONTROLS)) {
      if (control === null) continue;
      // "Mint invite" is gone once an invite is shown; the mint above proved it was there.
      if (operation === 'POST /v1/entrance/invites') continue;
      expect(present(control), operation).toBe(true);
    }
    expect(present(KEPT_ON_THE_STAND)).toBe(true);
  });
});

// The text of every source file under src/views, keyed by its path.
const SOURCES = import.meta.glob('../../../src/views/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

describe('the sources', () => {
  it('write each loopback-only path only in the two files whose controls are gated', () => {
    const allowed = ['use_device_actions.ts', 'use_enrol_actions.ts'];
    for (const operation of loopbackOnly()) {
      const path = operation.split(' ')[1]!;
      const writers = Object.entries(SOURCES)
        .filter(([, text]) => text.includes(`'${path}'`))
        .map(([file]) => file.split('/').pop()!);
      // A path with a control is written somewhere; one without is written nowhere in the views.
      expect(writers.length > 0, operation).toBe(CONTROLS[operation] !== null);
      for (const writer of writers) expect(allowed, `${operation} in ${writer}`).toContain(writer);
    }
  });
});
