/**
 * Mount helpers for the Devices and Enrol view tests: render a screen, open its socket, push a frame.
 *
 * Each screen is rendered directly under a `HiveProvider` built by `openHive`, because the app's
 * outlet does not point at them until the orchestrator registers the slices. Both open exactly one
 * socket (the Entrance's security stream), so the helper waits for it and opens it, as the other
 * views' tests do for their streams.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the Devices, Enrol and loopback gate tests.
 *
 * Key invariants:
 *   - Nothing here asserts beyond the socket having opened; a test asserts on `OpenedHive` itself.
 *
 * See Also:
 *   - tests/views/devices/support.ts for `openHive` and the builders.
 */
import { act, render, waitFor } from '@testing-library/react';
import type { ReactElement } from 'react';
import { expect } from 'vitest';
import { HiveProvider } from '../../../src/store';
import { flush } from '../../support/fake_browser';
import type { FakeSocket } from '../../support/fake_browser';
import { openHive } from './support';
import type { OpenedHive, Setup } from './support';

export interface Mounted extends OpenedHive {
  socket: FakeSocket;
}

// Renders `screen`, waits for its one security socket and opens it.
export async function mountScreen(screen: ReactElement, setup: Setup = {}): Promise<Mounted> {
  const opened = await openHive(setup);
  render(<HiveProvider hive={opened.hive}>{screen}</HiveProvider>);
  await waitFor(() => expect(opened.fake.sockets).toHaveLength(1));
  const socket = opened.fake.sockets[0]!;
  await act(async () => {
    socket.open();
    await flush();
  });
  return { ...opened, socket };
}

// Delivers one frame on the security socket and lets the view react to it.
export async function deliver(socket: FakeSocket, frame: unknown): Promise<void> {
  await act(async () => {
    socket.receive(frame);
    await flush();
  });
}
