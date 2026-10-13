/**
 * The Capping view's test harness: mount the route component over a hive and open its trail socket.
 *
 * Both view test files mount the screen the same way, so the step lives here once (a test file
 * must not import another test file, as that would run its tests twice).
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by capping_view.test.tsx and capping_reread.test.tsx.
 *
 * Key invariants:
 *   - The view opens exactly one socket, the trail stream; it is open when `mount` returns.
 *
 * See Also:
 *   - tests/views/capping/support.ts for the builders and `openHive`.
 */
import { act, render, waitFor } from '@testing-library/react';
import { expect } from 'vitest';
import { HiveProvider } from '../../../src/store';
import { CappingScreen } from '../../../src/views/capping';
import { flush } from '../../support/fake_browser';
import { openHive } from './support';
import type { OpenedHive } from './support';

type Answer = Parameters<typeof openHive>[0];

// Mounts the view, waits for its trail socket and opens it, as the other views' tests do.
export async function mount(answer?: Answer): Promise<OpenedHive> {
  const opened = await openHive(answer);
  render(
    <HiveProvider hive={opened.hive}>
      <CappingScreen />
    </HiveProvider>,
  );
  await waitFor(() => expect(opened.fake.sockets).toHaveLength(1));
  await act(async () => {
    opened.fake.sockets[0]!.open();
    await flush();
  });
  return opened;
}
