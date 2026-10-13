/**
 * Tests for the Honey reducer: folder seeds and pages, the reading pane, search, and the trail rule.
 *
 * The slice is pure, so each rule is checked on the reducer alone: a seed lands the folder, a later
 * page appends without repeating an entry, a reply for a folder, item or query the reader has left
 * changes nothing, the pane opens and closes, a search replaces and then extends its hits, and only
 * a trail event that concerns the open folder moves the counter the hook watches.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web tests). Exercises src/views/honey/slice/.
 *
 * Key invariants:
 *   - "Ignored" means the very same state object comes back, which the store reads as no change.
 *
 * See Also:
 *   - tests/views/honey/honey_view.test.tsx for the view and the re-read over this slice.
 */
import { describe, expect, it } from 'vitest';
import {
  cellOf,
  initialHoney,
  MAX_HITS,
  MAX_ITEMS,
  reduceHoney,
} from '../../../src/views/honey/slice';
import type { HoneyEvent, HoneyState } from '../../../src/views/honey/slice';
import { folderView, hitView, honeyTrail, itemSummary, itemView, searchPage } from './support';

const run = (events: HoneyEvent[], from: HoneyState = initialHoney): HoneyState =>
  events.reduce(reduceHoney, from);

const open: HoneyEvent = { type: 'open', path: '/hive' };
const seed = (over = {}): HoneyEvent => ({
  type: 'seeded',
  path: '/hive',
  folder: folderView(over),
});
const seeded = run([open, seed()]);

describe('folder seed', () => {
  it('lands the folder for the open path and clears an earlier failure', () => {
    const failed = run([open, { type: 'failed', path: '/hive', error: new Error('no') }]);
    expect(failed.error).toBeInstanceOf(Error);
    const state = run([seed()], failed);
    expect(state.folder?.path).toBe('/hive');
    expect(state.error).toBeNull();
  });

  it('ignores an answer, or a failure, for a folder the reader already left', () => {
    const opened = run([{ type: 'open', path: '/bee-bread' }]);
    expect(run([seed()], opened)).toBe(opened);
    expect(run([{ type: 'failed', path: '/hive', error: 1 }], opened)).toBe(opened);
  });

  it('starts another folder clean, with its item and search closed', () => {
    const busy = run(
      [
        { type: 'read_item', id: 'honey_1' },
        { type: 'search', path: '/hive', q: 'passkey' },
      ],
      seeded,
    );
    const next = run([{ type: 'open', path: '/cells/cell_1' }], busy);
    expect(next).toMatchObject({
      path: '/cells/cell_1',
      folder: null,
      reading: null,
      search: null,
    });
  });

  it('keeps what is held when the same folder is opened again', () => {
    expect(run([open], seeded)).toBe(seeded);
  });

  it('replaces the held folder on a re-read, so a cleared note is gone', () => {
    const fresh = run([seed({ items: [itemSummary({ id: 'honey_9' })] })], seeded);
    expect(fresh.folder?.items.map((item) => item.id)).toEqual(['honey_9']);
  });
});

describe('paging', () => {
  const first = seed({
    items: [itemSummary({ id: 'a' }), itemSummary({ id: 'b' })],
    next_cursor: 'c1',
  });
  const more = (cursor: string, ids: string[], next: string | null): HoneyEvent => ({
    type: 'more',
    path: '/hive',
    cursor,
    folder: folderView({ items: ids.map((id) => itemSummary({ id })), next_cursor: next }),
  });

  it('appends the next page without repeating an item, and takes its cursor', () => {
    const state = run([open, first, more('c1', ['b', 'c'], null)]);
    expect(state.folder?.items.map((item) => item.id)).toEqual(['a', 'b', 'c']);
    expect(state.folder?.next_cursor).toBeNull();
  });

  it('appends sub-folders by path as well', () => {
    const entry = (path: string) => ({ detail: '', name: path.slice(1), path, title: path });
    const state = run([
      open,
      seed({ folders: [entry('/x')], next_cursor: 'c1' }),
      {
        type: 'more',
        path: '/hive',
        cursor: 'c1',
        folder: folderView({ folders: [entry('/x'), entry('/y')] }),
      },
    ]);
    expect(state.folder?.folders.map((folder) => folder.path)).toEqual(['/x', '/y']);
  });

  it('ignores a page whose cursor was already taken', () => {
    const once = run([open, first, more('c1', ['c'], null)]);
    expect(run([more('c1', ['d'], null)], once)).toBe(once);
  });

  it('cuts at the bound and drops the cursor, so no row is skipped', () => {
    const ids = Array.from({ length: MAX_ITEMS }, (_, i) => `i${i}`);
    const full = run([
      open,
      seed({ items: ids.map((id) => itemSummary({ id })), next_cursor: 'c1' }),
    ]);
    const state = run([more('c1', ['extra'], 'c2')], full);
    expect(state.folder?.items).toHaveLength(MAX_ITEMS);
    expect(state.folder?.next_cursor).toBeNull();
    expect(state.truncated).toBe(true);
  });
});

describe('the reading pane', () => {
  it('opens at once, fills when the item lands and closes', () => {
    const reading = run([{ type: 'read_item', id: 'honey_1' }], seeded);
    expect(reading.reading).toEqual({ id: 'honey_1', item: null, error: null });
    const filled = run([{ type: 'item', id: 'honey_1', item: itemView() }], reading);
    expect(filled.reading?.item?.content).toContain('passkey');
    expect(run([{ type: 'close_item' }], filled).reading).toBeNull();
  });

  it('records a refusal against the item that was open', () => {
    const state = run(
      [
        { type: 'read_item', id: 'honey_1' },
        { type: 'item_failed', id: 'honey_1', error: 'no' },
      ],
      seeded,
    );
    expect(state.reading).toEqual({ id: 'honey_1', item: null, error: 'no' });
  });

  it('drops a reply for an item that is no longer open', () => {
    const second = run(
      [
        { type: 'read_item', id: 'honey_1' },
        { type: 'read_item', id: 'honey_2' },
      ],
      seeded,
    );
    expect(run([{ type: 'item', id: 'honey_1', item: itemView() }], second)).toBe(second);
    expect(run([{ type: 'item_failed', id: 'honey_1', error: 1 }], second)).toBe(second);
    expect(
      run([{ type: 'close_item' }, { type: 'item', id: 'honey_2', item: itemView() }], second)
        .reading,
    ).toBeNull();
  });

  it('closing a closed pane changes nothing', () => {
    expect(run([{ type: 'close_item' }], seeded)).toBe(seeded);
  });
});

describe('search', () => {
  const start: HoneyEvent = { type: 'search', path: '/hive', q: 'passkey' };
  const found = (cursor: string | null, ids: string[], next: string | null): HoneyEvent => ({
    type: 'found',
    path: '/hive',
    q: 'passkey',
    cursor,
    page: searchPage({
      hits: ids.map((id) => hitView({ id })),
      next_cursor: next,
      note: next ? '' : 'That is all.',
    }),
  });

  it('is pending until the first page lands, then holds the hits and the page note', () => {
    const pending = run([start], seeded);
    expect(pending.search).toMatchObject({ q: 'passkey', done: false, hits: [] });
    const done = run([found(null, ['h1'], null)], pending);
    expect(done.search).toMatchObject({ done: true, note: 'That is all.' });
    expect(done.search?.hits.map((hit) => hit.id)).toEqual(['h1']);
  });

  it('extends its hits by cursor without repeating one, and ignores a repeated cursor', () => {
    const one = run([start, found(null, ['h1', 'h2'], 'p2')], seeded);
    const two = run([found('p2', ['h2', 'h3'], null)], one);
    expect(two.search?.hits.map((hit) => hit.id)).toEqual(['h1', 'h2', 'h3']);
    expect(two.search?.nextCursor).toBeNull();
    expect(run([found('p2', ['h4'], null)], two)).toBe(two);
  });

  it('drops a reply for another query or folder, and a new search clears the old hits', () => {
    const one = run([start, found(null, ['h1'], null)], seeded);
    const other: HoneyEvent = { ...found(null, ['x'], null), q: 'other' } as HoneyEvent;
    expect(run([other], one)).toBe(one);
    const again = run([{ type: 'search', path: '/hive', q: 'vault' }], one);
    expect(again.search).toMatchObject({ q: 'vault', hits: [], done: false });
    expect(run([{ type: 'search', path: '/bee-bread', q: 'x' }], one)).toBe(one);
  });

  it('records a failure against the query and clears on request', () => {
    const failed = run(
      [start, { type: 'search_failed', path: '/hive', q: 'passkey', error: 'no' }],
      seeded,
    );
    expect(failed.search).toMatchObject({ done: true, error: 'no' });
    expect(run([{ type: 'clear_search' }], failed).search).toBeNull();
  });

  it('cuts at the hit bound', () => {
    const ids = Array.from({ length: MAX_HITS + 1 }, (_, i) => `h${i}`);
    const state = run([start, found(null, ids, 'more')], seeded);
    expect(state.search?.hits).toHaveLength(MAX_HITS);
    expect(state.search).toMatchObject({ nextCursor: null, truncated: true });
  });
});

describe('the trail rule', () => {
  const ticks = (path: string, frame: ReturnType<typeof honeyTrail>): number =>
    run([
      { type: 'open', path },
      { type: 'seeded', path, folder: folderView({ path }) },
      { type: 'trail', frame },
    ]).trailTicks;

  it('counts nothing before the folder is seeded', () => {
    expect(run([open, { type: 'trail', frame: honeyTrail() }]).trailTicks).toBe(0);
  });

  it('counts any honey.* event for a folder that is not a Cell’s', () => {
    for (const path of ['/', '/hive', '/bees/worker_1', '/tasks/task_1', '/bee-bread', '/cells']) {
      expect(ticks(path, honeyTrail())).toBe(1);
    }
    expect(ticks('/hive', honeyTrail({ kind: 'memory.wax_written', subject_id: 'cell_1' }))).toBe(
      0,
    );
    expect(ticks('/hive', honeyTrail({ kind: 'task.assigned' }))).toBe(0);
  });

  it('counts a honey or wax event for a Cell’s folder only when it names that Cell', () => {
    const wax = honeyTrail({ kind: 'memory.wax_written', subject_id: 'cell_1' });
    expect(ticks('/cells/cell_1', wax)).toBe(1);
    expect(ticks('/cells/cell_1/wax', wax)).toBe(1);
    expect(ticks('/cells/cell_2', wax)).toBe(0);
    expect(ticks('/cells/cell_1', honeyTrail())).toBe(0);
    expect(ticks('/cells/cell_1', honeyTrail({ payload: { cell_id: 'cell_1' } }))).toBe(1);
    expect(ticks('/cells/cell_1', honeyTrail({ actor: 'cell_1' }))).toBe(1);
    expect(ticks('/cells/cell_1', honeyTrail({ kind: 'alarm.raised', subject_id: 'cell_1' }))).toBe(
      0,
    );
  });

  it('names the Cell of a path', () => {
    expect(cellOf('/cells/cell_1/wax')).toBe('cell_1');
    expect(cellOf('/cells')).toBeNull();
    expect(cellOf('/hive')).toBeNull();
  });
});
