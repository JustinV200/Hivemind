/**
 * Mounts the Honey screen on the harness store and opens its one socket, for the view tests.
 *
 * The screen opens the trail stream when it mounts. A test needs that socket open before it sends
 * a frame, so this helper renders the screen, waits for the socket to exist, opens it and lets the
 * first folder read settle. It returns the harness and the rendered view so the test can unmount,
 * and `openItem`, which re-renders the screen with another item in its address, as a click on an
 * item's link would do through the router.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Used by the view and live tests under tests/views/honey/.
 *
 * Key invariants:
 *   - Exactly one socket is expected (the trail); a second would make `waitFor` pass too early
 *     for a test that counts them, so tests assert the count themselves.
 *
 * See Also:
 *   - tests/views/honey/support.ts for the harness and builders.
 */
import { act, render, waitFor } from '@testing-library/react';
import { expect } from 'vitest';
import { HiveProvider } from '../../../src/store';
import { HoneyScreen } from '../../../src/views/honey';
import { flush } from '../../support/fake_browser';
import { openHive } from './support';
import type { OpenedHive } from './support';

export type Mounted = OpenedHive & {
  view: ReturnType<typeof render>;
  openItem: (item: string | null) => void;
};

const screenOf = (hive: OpenedHive['hive'], path: string, item: string | null) => (
  <HiveProvider hive={hive}>
    <HoneyScreen path={path} item={item} />
  </HiveProvider>
);

// `setup` runs after the harness exists and before the screen reads, to script the routes.
export async function mount(
  path: string,
  setup: (opened: OpenedHive) => void,
  item: string | null = null,
): Promise<Mounted> {
  const opened = await openHive();
  setup(opened);
  const view = render(screenOf(opened.hive, path, item));
  await waitFor(() => expect(opened.fake.sockets).toHaveLength(1));
  await act(async () => {
    opened.fake.sockets[0]!.open();
    await flush();
  });
  return { ...opened, view, openItem: (id) => view.rerender(screenOf(opened.hive, path, id)) };
}
