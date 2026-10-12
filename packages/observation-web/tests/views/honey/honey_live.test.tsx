/**
 * Tests for what the Honey browser shows per viewer, and for when it reads a folder again.
 *
 * Scope: two viewers with different scopes are served two different scripted answers for the same
 * paths (the Entrance's job), and the rendered trees must differ exactly as the folder views say,
 * with a folder a viewer may not see absent from its parent and refused when opened by address.
 * Live: the Honey Store has no stream, so a folder is read again only because the trail showed a
 * Honey or Cell Wax event for it, once per burst a second later, never without a frame.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/honey/ through its route component.
 *
 * Key invariants:
 *   - The re-read tests use real time (the delay is one second), so they are the slow ones.
 *   - A viewer's tree is whatever its scripted answers say; the view adds and removes nothing.
 *
 * See Also:
 *   - tests/views/honey/honey_view.test.tsx for the folder, item and search views.
 *   - tests/views/honey/slice.test.ts for the trail rule on the reducer alone.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { mount } from './mount';
import type { Mounted } from './mount';
import {
  FORBIDDEN,
  folderView,
  honeyTrail,
  itemSummary,
  requestsTo,
  scriptTree,
  waxSummary,
} from './support';

const wait = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
// Longer than the one second window, so a read that was going to happen would have.
const PAST_WINDOW_MS = 1250;

const entry = (path: string, title: string) => ({
  detail: '',
  name: path.split('/').pop() ?? '',
  path,
  title,
});

// The sub-folder links and item titles a rendered folder shows, in order.
function shown(): { folders: string[]; items: string[]; ceiling: string } {
  const folders = screen
    .queryAllByRole('link')
    .filter(
      (link) => link.getAttribute('href')?.startsWith('#/honey/') && link.closest('.honey-folders'),
    )
    .map((link) => link.getAttribute('href') ?? '');
  const items = Array.from(document.querySelectorAll('.honey-items .honey-open')).map(
    (button) => button.textContent ?? '',
  );
  const ceiling =
    within(screen.getByText(/You may read up to/)).getByText(/^C\d/).textContent ?? '';
  return { folders, items, ceiling };
}

const reads = (opened: Mounted) => requestsTo(opened.fake, '/v1/honey/tree').length;

describe('two viewers with different scopes', () => {
  const root = folderView({ path: '/', parent: null, scope: null, can_search: true });
  const wide = {
    '/': {
      ...root,
      ceiling: 'C2' as const,
      folders: [entry('/hive', 'Shared'), entry('/cells', 'Cells'), entry('/bees', 'Bees')],
      items: [],
    },
    '/cells/cell_1': folderView({
      path: '/cells/cell_1',
      parent: '/cells',
      scope: 'cell:cell_1',
      ceiling: 'C2',
      folders: [entry('/cells/cell_1/wax', 'Live Cell Wax')],
      items: [itemSummary({ id: 'h2', title: 'Private history', clearance: 'C2' }), itemSummary()],
    }),
  };
  const narrow = {
    '/': { ...root, ceiling: 'C1' as const, folders: [entry('/hive', 'Shared')], items: [] },
    '/cells/cell_1': FORBIDDEN,
  };

  it('render root trees that differ exactly as the folder views say', async () => {
    const wideView = await mount('', ({ fake }) => scriptTree(fake, wide));
    await screen.findByText('Shared');
    const withScopes = shown();
    wideView.view.unmount();
    const narrowView = await mount('', ({ fake }) => scriptTree(fake, narrow));
    await screen.findByText('Shared');
    const without = shown();
    narrowView.view.unmount();

    expect(withScopes.folders).toEqual(['#/honey/hive', '#/honey/cells', '#/honey/bees']);
    expect(without.folders).toEqual(['#/honey/hive']);
    // Exactly the folders the narrower viewer lacks are the ones absent, and the ceiling differs.
    expect(withScopes.folders.filter((href) => !without.folders.includes(href))).toEqual([
      '#/honey/cells',
      '#/honey/bees',
    ]);
    expect([withScopes.ceiling, without.ceiling]).toEqual(['C2 Royal', 'C1 Apiary']);
  });

  it('render a Cell’s folder only for the viewer whose scopes reach it', async () => {
    const wideView = await mount('cells/cell_1', ({ fake }) => scriptTree(fake, wide));
    await screen.findByText('Private history');
    const withScopes = shown();
    expect(withScopes.folders).toEqual(['#/honey/cells/cell_1/wax']);
    expect(withScopes.items).toEqual(['Private history', 'How the site logs in']);
    wideView.view.unmount();

    // The same address for the viewer without the scope: the refusal sentence, and no folder.
    await mount('cells/cell_1', ({ fake }) => scriptTree(fake, narrow));
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(screen.queryByText('Private history')).toBeNull();
    expect(screen.queryByText(/You may read up to/)).toBeNull();
  });

  it('render a Cell’s wax folder with the notes each viewer’s allowance reaches', async () => {
    const wax = (items: ReturnType<typeof waxSummary>[], ceiling: 'C1' | 'C2') =>
      folderView({ path: '/cells/cell_1/wax', parent: '/cells/cell_1', ceiling, items });
    const first = await mount('cells/cell_1/wax', ({ fake }) =>
      scriptTree(fake, {
        '/cells/cell_1/wax': wax(
          [waxSummary(), waxSummary({ id: 'w2', title: 'Owner habits', clearance: 'C2' })],
          'C2',
        ),
      }),
    );
    await screen.findByText('Owner habits');
    expect(shown().items).toEqual(['Disk fills at night', 'Owner habits']);
    first.view.unmount();
    await mount('cells/cell_1/wax', ({ fake }) =>
      scriptTree(fake, { '/cells/cell_1/wax': wax([waxSummary()], 'C1') }),
    );
    await screen.findByText('Disk fills at night');
    expect(shown().items).toEqual(['Disk fills at night']);
    expect(screen.queryByText('Owner habits')).toBeNull();
  });
});

describe('the re-read', () => {
  const cellFolder = (items = [waxSummary()]) =>
    folderView({ path: '/cells/cell_1', parent: '/cells', scope: 'cell:cell_1', items });
  const mountCell = (table: Record<string, ReturnType<typeof cellFolder>>) =>
    mount('cells/cell_1', ({ fake }) => scriptTree(fake, table));
  const send = (opened: Mounted, ...frames: ReturnType<typeof honeyTrail>[]) =>
    act(() => frames.forEach((frame) => opened.fake.sockets[0]!.receive(frame)));

  it('makes no read without a frame, and none for a frame about nothing in this folder', async () => {
    const opened = await mountCell({ '/cells/cell_1': cellFolder() });
    await screen.findByText('Disk fills at night');
    // Another Cell's wax, a Honey event naming no Cell, and an event of another family.
    send(
      opened,
      honeyTrail({ kind: 'memory.wax_written', subject_id: 'cell_2' }),
      honeyTrail({ kind: 'honey.ripened', subject_id: 'nectar_1' }),
      honeyTrail({ kind: 'alarm.raised', subject_id: 'cell_1' }),
    );
    await wait(PAST_WINDOW_MS);
    expect(reads(opened)).toBe(1);
  });

  it('reads a Cell’s folder once, a second after the first Cell Wax frame of a burst', async () => {
    const table = { '/cells/cell_1': cellFolder() };
    const opened = await mountCell(table);
    await screen.findByText('Disk fills at night');
    table['/cells/cell_1'] = cellFolder([
      waxSummary(),
      waxSummary({ id: 'w2', title: 'New caution' }),
    ]);
    send(
      opened,
      honeyTrail({ id: 'e1', kind: 'memory.wax_written', subject_id: 'cell_1' }),
      honeyTrail({ id: 'e2', kind: 'memory.wax_expired', subject_id: 'cell_1' }),
      honeyTrail({ id: 'e3', kind: 'honey.ripened', payload: { cell_id: 'cell_1' } }),
    );
    // Not before the window closes.
    await wait(400);
    expect(reads(opened)).toBe(1);
    expect(await screen.findByText('New caution', {}, { timeout: 2500 })).toBeTruthy();
    expect(reads(opened)).toBe(2);
    // The three frames cost one read: nothing else follows.
    await wait(PAST_WINDOW_MS);
    expect(reads(opened)).toBe(2);
  });

  it('reads any other folder on any Honey event, and not on a wax event', async () => {
    const table = { '/hive': folderView() };
    const opened = await mount('hive', ({ fake }) => scriptTree(fake, table));
    await screen.findByText('How the site logs in');
    send(opened, honeyTrail({ kind: 'memory.wax_written', subject_id: 'cell_1' }));
    await wait(PAST_WINDOW_MS);
    expect(reads(opened)).toBe(1);
    table['/hive'] = folderView({ items: [itemSummary({ id: 'n', title: 'Fresh row' })] });
    send(opened, honeyTrail({ kind: 'honey.ripened', subject_id: 'nectar_9' }));
    expect(await screen.findByText('Fresh row', {}, { timeout: 2500 })).toBeTruthy();
    expect(reads(opened)).toBe(2);
  });

  it('asks for as many entries as the reader has paged in', async () => {
    const page = (from: number, count: number, next: string | null) =>
      folderView({
        items: Array.from({ length: count }, (_, i) =>
          itemSummary({ id: `r${from + i}`, title: `Row ${from + i}` }),
        ),
        next_cursor: next,
      });
    const opened = await mount('hive', ({ fake }) =>
      scriptTree(fake, { '/hive': page(0, 50, 'c1'), '/hive|c1': page(50, 10, null) }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    await screen.findByText('Row 59');
    send(opened, honeyTrail());
    await waitFor(() => expect(reads(opened)).toBe(3), { timeout: 2500 });
    const last = requestsTo(opened.fake, '/v1/honey/tree').at(-1);
    expect(last?.query.get('limit')).toBe('60');
    expect(last?.query.get('cursor')).toBeNull();
  });

  it('cancels a pending read when the reader leaves', async () => {
    const opened = await mountCell({ '/cells/cell_1': cellFolder() });
    await screen.findByText('Disk fills at night');
    send(opened, honeyTrail({ kind: 'memory.wax_written', subject_id: 'cell_1' }));
    opened.view.unmount();
    await wait(PAST_WINDOW_MS);
    expect(reads(opened)).toBe(1);
  });
});
