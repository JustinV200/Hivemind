/**
 * Tests for the Honey browser view: folders, items, wax fields, reading, search and refusals.
 *
 * The screen is rendered directly over a store that holds the app's slices plus Honey's (the app
 * table does not hold it until the orchestrator registers it), on the real client and the fake
 * browser. A test scripts the Entrance's three routes for the viewer it is about, mounts a path and
 * drives the page the way a reader would.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/honey/ through its route component.
 *
 * Key invariants:
 *   - Every request the view makes is a GET (it is read-only), and no propose control exists.
 *   - A refusal shows the table's sentence and never the scope or label that refused it.
 *
 * See Also:
 *   - tests/views/honey/honey_live.test.tsx for scope differences and the trail re-read.
 *   - tests/views/honey/slice.test.ts for the reducer rules.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FakeAnswer } from '../../support/fake_browser';
import { mount } from './mount';
import {
  FORBIDDEN,
  folderView,
  GONE,
  hitView,
  itemSummary,
  itemView,
  provenance,
  requestsTo,
  scriptSearch,
  scriptTree,
  searchPage,
  waxSummary,
} from './support';

const rowOf = (title: string): HTMLElement =>
  screen.getByRole('link', { name: title }).closest('li') as HTMLElement;

const SUB = { detail: '3 rows', name: 'sub', path: '/hive/sub', title: 'Sub topics' };

describe('a folder', () => {
  const folder = folderView({
    note: 'Rows above C1 are not listed.',
    folders: [SUB],
    items: [itemSummary(), waxSummary()],
  });
  const open = () => mount('hive', ({ fake }) => scriptTree(fake, { '/hive': folder }));

  it('shows breadcrumbs from the path, an Up link from the parent, the ceiling and the note', async () => {
    await open();
    const crumbs = await screen.findByRole('navigation', { name: 'Honey folder' });
    expect(within(crumbs).getByRole('link', { name: 'Honey' }).getAttribute('href')).toBe(
      '#/honey',
    );
    expect(within(crumbs).getByText('hive').getAttribute('aria-current')).toBe('page');
    expect(within(crumbs).getByRole('link', { name: 'Up' }).getAttribute('href')).toBe('#/honey');
    expect(screen.getByText(/You may read up to/).textContent).toContain('C1 Apiary');
    expect(screen.getByText('Rows above C1 are not listed.')).toBeTruthy();
  });

  it('lists sub-folders as links the router resolves', async () => {
    await open();
    const link = await screen.findByRole('link', { name: 'Sub topics' });
    expect(link.getAttribute('href')).toBe('#/honey/hive/sub');
    expect(screen.getByText('3 rows')).toBeTruthy();
  });

  it('lists each item with its clearance tag, kind, detail and provenance', async () => {
    await open();
    await screen.findByText('How the site logs in');
    const row = within(rowOf('How the site logs in'));
    expect(row.getByText('C1 Apiary')).toBeTruthy();
    expect(row.getByText('Honey')).toBeTruthy();
    expect(row.getByText('part 1')).toBeTruthy();
    for (const label of ['Task', 'Cell', 'Bee', 'Source', 'When']) {
      expect(row.getByText(label)).toBeTruthy();
    }
    expect(row.getByText('task_1')).toBeTruthy();
    expect(row.getByText('BEE')).toBeTruthy();
    expect(row.getByTitle('2026-01-01T00:00:00Z').getAttribute('datetime')).toBe(
      '2026-01-01T00:00:00Z',
    );
  });

  it('shows a Cell Wax note’s severity, proposer and expiry, and its Royal label', async () => {
    await mount('cells/cell_1/wax', ({ fake }) =>
      scriptTree(fake, {
        '/cells/cell_1/wax': folderView({
          path: '/cells/cell_1/wax',
          parent: '/cells/cell_1',
          ceiling: 'C2',
          can_search: false,
          items: [
            waxSummary({ clearance: 'C2' }),
            waxSummary({
              id: 'wax_2',
              title: 'Standing',
              severity: 'BLOCK',
              proposer: null,
              expires_at: null,
            }),
          ],
        }),
      }),
    );
    await screen.findByText('Disk fills at night');
    const first = within(rowOf('Disk fills at night'));
    expect(first.getByText('C2 Royal')).toBeTruthy();
    expect(first.getByText('Cell Wax')).toBeTruthy();
    expect(first.getByText('CAUTION')).toBeTruthy();
    expect(first.getByText('warden_1')).toBeTruthy();
    expect(first.getByTitle('2026-02-01T00:00:00Z')).toBeTruthy();
    const second = within(rowOf('Standing'));
    expect(second.getByText('BLOCK')).toBeTruthy();
    expect(second.getByText('the human')).toBeTruthy();
    expect(second.getByText('no expiry')).toBeTruthy();
  });

  it('says plainly that an empty folder is empty', async () => {
    await mount('bee-bread', ({ fake }) =>
      scriptTree(fake, {
        '/bee-bread': folderView({ path: '/bee-bread', items: [], can_search: false }),
      }),
    );
    expect(
      await screen.findByText('Nothing in this folder that this device may see.'),
    ).toBeTruthy();
  });

  it('pages on by cursor and appends without repeating an item', async () => {
    const opened = await mount('hive', ({ fake }) =>
      scriptTree(fake, {
        '/hive': folderView({
          items: [itemSummary({ id: 'a', title: 'First' })],
          next_cursor: 'c1',
        }),
        '/hive|c1': folderView({
          items: [
            itemSummary({ id: 'a', title: 'First' }),
            itemSummary({ id: 'b', title: 'Second' }),
          ],
        }),
      }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    expect(await screen.findByText('Second')).toBeTruthy();
    expect(screen.getAllByText('First')).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    expect(requestsTo(opened.fake, '/v1/honey/tree').at(-1)?.query.get('cursor')).toBe('c1');
  });

  it('draws no propose control', async () => {
    await open();
    await screen.findByText('How the site logs in');
    // No line advertises the absent feature either.
    expect(screen.queryByText(/proposing a note/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /propos/i })).toBeNull();
    expect(screen.queryByRole('link', { name: /propos/i })).toBeNull();
    expect(screen.queryByRole('textbox', { name: /note/i })).toBeNull();
  });
});

describe('reading an item', () => {
  // The item is in the address (`#/honey/hive?item=honey_1`), so the screen mounts with it open.
  const mountRow = (answer: FakeAnswer) =>
    mount(
      'hive',
      ({ fake }) => {
        scriptTree(fake, { '/hive': folderView() });
        fake.respond('GET /v1/honey/items/honey_1', answer.status, answer.body);
      },
      'honey_1',
    );

  it('opens the whole item: label, provenance, summary, content, reason and other sources', async () => {
    const opened = await mountRow({
      status: 200,
      body: itemView({
        summary: 'Passkey login',
        reason: 'Seen twice on the site',
        sources: [provenance({ bee: 'worker_2', task_id: 'task_2' })],
      }),
    });
    const pane = await screen.findByLabelText('Item');
    const body = within(pane);
    expect(await body.findByRole('heading', { name: 'How the site logs in' })).toBeTruthy();
    // The row's link is the pane's address, so the open item can be shared and reloaded.
    expect(
      (await screen.findByRole('link', { name: 'How the site logs in' })).getAttribute('href'),
    ).toBe('#/honey/hive?item=honey_1');
    expect(body.getByText('C1 Apiary')).toBeTruthy();
    expect(body.getByText('Passkey login')).toBeTruthy();
    expect(body.getByText('Seen twice on the site')).toBeTruthy();
    expect(body.getByText('worker_2')).toBeTruthy();
    // The content keeps its line break and indentation, in its own scrolling box.
    expect(pane.querySelector('pre')?.textContent).toBe(
      'Log in with the saved passkey.\n  Then open the board.',
    );
    expect(requestsTo(opened.fake, '/v1/honey/items/honey_1')).toHaveLength(1);
  });

  it('closes the pane when the address drops the item, which Close links to', async () => {
    const opened = await mountRow({ status: 200, body: itemView() });
    const pane = await screen.findByLabelText('Item');
    expect(within(pane).getByRole('link', { name: 'Close' }).getAttribute('href')).toBe(
      '#/honey/hive',
    );
    opened.openItem(null);
    expect(screen.queryByLabelText('Item')).toBeNull();
  });

  it('shows the refusal sentence without naming the scope or the label', async () => {
    await mountRow(FORBIDDEN);
    const pane = await screen.findByLabelText('Item');
    expect(
      await within(pane).findByText('This device is not allowed to see or do that.'),
    ).toBeTruthy();
    expect(pane.textContent).not.toMatch(/observe:honey|scope|cell:|C2/);
  });

  it('says a 404 item is gone', async () => {
    await mountRow(GONE);
    expect(await screen.findByText(/That item is gone/)).toBeTruthy();
  });
});

describe('search', () => {
  it('has no search box for a folder that cannot be searched', async () => {
    await mount('bee-bread', ({ fake }) =>
      scriptTree(fake, { '/bee-bread': folderView({ path: '/bee-bread', can_search: false }) }),
    );
    await screen.findByText('How the site logs in');
    expect(screen.queryByLabelText('Search this folder')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Search' })).toBeNull();
  });

  it('searches the open folder, shows the hits, pages by cursor and opens a hit', async () => {
    const opened = await mount('hive', ({ fake }) => {
      scriptTree(fake, { '/hive': folderView() });
      scriptSearch(fake, {
        passkey: searchPage({ next_cursor: 'p2' }),
        'passkey|p2': searchPage({
          hits: [hitView({ id: 'honey_3', title: 'Second hit', clearance: 'C2' })],
          note: 'No more hits for those words.',
        }),
      });
      fake.respond(
        'GET /v1/honey/items/honey_2',
        200,
        itemView({ id: 'honey_2', title: 'Where the passkey lives' }),
      );
    });
    fireEvent.change(await screen.findByLabelText('Search this folder'), {
      target: { value: ' passkey ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    const hit = await screen.findByRole('link', { name: 'Where the passkey lives' });
    expect(hit.getAttribute('href')).toBe('#/honey/hive?item=honey_2');
    // The hits stand under their own heading, in place of the folder's items.
    expect(screen.getByRole('heading', { name: 'Results for "passkey" (1 so far)' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Items' })).toBeNull();
    const row = within(hit.closest('li') as HTMLElement);
    expect(row.getByText('C1 Apiary')).toBeTruthy();
    expect(row.getByText('Score 0.83')).toBeTruthy();
    expect(row.getByText('the passkey is kept in the vault')).toBeTruthy();
    expect(row.getByText('task_1')).toBeTruthy();
    expect(requestsTo(opened.fake, '/v1/honey/search')[0]?.query.get('path')).toBe('/hive');
    expect(requestsTo(opened.fake, '/v1/honey/search')[0]?.query.get('q')).toBe('passkey');

    fireEvent.click(screen.getByRole('button', { name: 'More results' }));
    expect(await screen.findByText('Second hit')).toBeTruthy();
    expect(screen.getByText('Where the passkey lives')).toBeTruthy();
    expect(screen.getByText('No more hits for those words.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'More results' })).toBeNull();

    // The hit's link opens the item through the address, as the folder's rows do.
    opened.openItem('honey_2');
    expect(
      await within(await screen.findByLabelText('Item')).findByText(/Log in with the saved/),
    ).toBeTruthy();
  });

  it('says so when nothing matches, and clears on request', async () => {
    await mount('hive', ({ fake }) => {
      scriptTree(fake, { '/hive': folderView() });
      scriptSearch(fake, { nothing: searchPage({ hits: [] }) });
    });
    fireEvent.change(await screen.findByLabelText('Search this folder'), {
      target: { value: 'nothing' },
    });
    fireEvent.submit(screen.getByRole('search'));
    expect(await screen.findByText('No matches for "nothing".')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.queryByText(/No matches/)).toBeNull();
    // The folder's own items are back once the search is cleared.
    expect(screen.getByRole('heading', { name: 'Items' })).toBeTruthy();
  });

  it('shows a search refusal through the error notice', async () => {
    await mount('hive', ({ fake }) => {
      scriptTree(fake, { '/hive': folderView() });
      scriptSearch(fake, { secret: FORBIDDEN });
    });
    fireEvent.change(await screen.findByLabelText('Search this folder'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
  });
});

describe('refusals and the whole screen', () => {
  it('shows the refusal sentence for a folder the device may not see, and a way back', async () => {
    await mount('cells/cell_9', ({ fake }) => scriptTree(fake, { '/cells/cell_9': FORBIDDEN }));
    expect(await screen.findByText('This device is not allowed to see or do that.')).toBeTruthy();
    expect(
      screen.getByRole('link', { name: 'Back to the top of the Honey' }).getAttribute('href'),
    ).toBe('#/honey');
    expect(screen.queryByText(/You may read up to/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/observe:honey|cell:cell_9/);
  });

  it('shows the Entrance’s sentence for a path that names no folder', async () => {
    await mount('nowhere', ({ fake }) => scriptTree(fake, {}));
    await waitFor(() =>
      expect(
        document.querySelector('[data-code="hivemind.entrance.honey_item_not_found"]'),
      ).toBeTruthy(),
    );
  });

  it('only reads: every request is a GET, and the one stream is the trail', async () => {
    const opened = await mount('hive', ({ fake }) => {
      scriptTree(fake, { '/hive': folderView({ next_cursor: 'c1' }), '/hive|c1': folderView() });
      scriptSearch(fake, { passkey: searchPage() });
      fake.respond('GET /v1/honey/items/honey_1', 200, itemView());
    });
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    opened.openItem('honey_1');
    await screen.findByLabelText('Item');
    fireEvent.change(screen.getByLabelText('Search this folder'), { target: { value: 'passkey' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    await screen.findByText('Where the passkey lives');
    expect(opened.fake.requests.length).toBeGreaterThan(3);
    expect(opened.fake.requests.every((request) => request.method === 'GET')).toBe(true);
    expect(opened.fake.sockets.map((s) => s.url.replace('wss://hive.test', ''))).toEqual([
      '/v1/trail/stream',
    ]);
  });

  it('closes its stream when the reader leaves', async () => {
    const opened = await mount('hive', ({ fake }) => scriptTree(fake, { '/hive': folderView() }));
    await waitFor(() => expect(screen.getByText('How the site logs in')).toBeTruthy());
    expect(opened.fake.sockets[0]!.closedWith).toBeNull();
    opened.view.unmount();
    expect(opened.fake.sockets[0]!.closedWith).toBe(1000);
  });
});
