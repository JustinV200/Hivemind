/**
 * The Honey reducer: folder seeds and pages, the reading pane and the folder's search.
 *
 * The view seeds the slice from `GET /v1/honey/tree`, appends later pages by cursor, and counts
 * the trail events that concern the open folder so the hook can read again once per burst. The
 * reading pane and the search live beside the folder and are cleared when another folder opens.
 * Every reply carries the path, id or query it answers and is dropped when the reader has moved on.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/slice/`. Run by the store for every dispatch to
 *   the `honey` slice; the events come from `use_folder.ts`, `use_reading.ts` and `use_search.ts`.
 *
 * Key invariants:
 *   - Pure and bounded: at most `MAX_ITEMS` entries and `MAX_HITS` hits; no clock, no I/O.
 *   - A stale reply (another folder, item, query or an already-taken cursor) returns the very same
 *     state object, which the store reads as no change.
 *   - Nothing is counted before the folder's seed lands, and an event that concerns another
 *     folder, or none, changes nothing.
 *
 * See Also:
 *   - src/views/honey/slice/fold.ts for the merges and the trail rule.
 */
import { appendHits, appendPage, concerns } from './fold';
import { initialHoney } from './state';
import type { HoneyEvent, HoneyState, SearchState } from './state';

function opened(state: HoneyState, path: string): HoneyState {
  // The same folder again keeps what is held (the reseed replaces it); another starts clean.
  if (path === state.path) return state.error ? { ...state, error: null } : state;
  return { ...initialHoney, path };
}

function paged(state: HoneyState, event: HoneyEvent & { type: 'more' }): HoneyState {
  const held = state.folder;
  // A reply for a page already taken (a double click) is ignored.
  if (!held || event.path !== state.path || event.cursor !== held.next_cursor) return state;
  const { folder, cut } = appendPage(held, event.folder);
  return { ...state, folder, truncated: state.truncated || cut, error: null };
}

function reduceFolder(state: HoneyState, event: HoneyEvent): HoneyState {
  switch (event.type) {
    case 'open':
      return opened(state, event.path);
    case 'seeded':
      return event.path === state.path
        ? { ...state, folder: event.folder, truncated: false, error: null }
        : state;
    case 'more':
      return paged(state, event);
    case 'failed':
      return event.path === state.path ? { ...state, error: event.error } : state;
    case 'trail':
      return state.folder && state.path && concerns(state.path, event.frame.event)
        ? { ...state, trailTicks: state.trailTicks + 1 }
        : state;
    default:
      return state;
  }
}

function reduceReading(state: HoneyState, event: HoneyEvent): HoneyState {
  const reading = state.reading;
  switch (event.type) {
    case 'read_item':
      return { ...state, reading: { id: event.id, item: null, error: null } };
    case 'item':
      return reading?.id === event.id
        ? { ...state, reading: { id: event.id, item: event.item, error: null } }
        : state;
    case 'item_failed':
      return reading?.id === event.id
        ? { ...state, reading: { id: event.id, item: null, error: event.error } }
        : state;
    case 'close_item':
      return reading ? { ...state, reading: null } : state;
    default:
      return state;
  }
}

const pending = (q: string): SearchState => ({
  q,
  hits: [],
  nextCursor: null,
  note: '',
  done: false,
  truncated: false,
  error: null,
});

function found(state: HoneyState, event: HoneyEvent & { type: 'found' }): HoneyState {
  const held = state.search;
  if (!held || event.path !== state.path || event.q !== held.q) return state;
  // A later page must continue exactly where the held one stopped; the first replaces the hits.
  if (event.cursor !== null && event.cursor !== held.nextCursor) return state;
  const { hits, cut } = appendHits(event.cursor === null ? [] : held.hits, event.page.hits);
  const next: SearchState = {
    ...held,
    hits,
    nextCursor: cut ? null : event.page.next_cursor,
    note: event.page.note,
    done: true,
    truncated: held.truncated || cut,
    error: null,
  };
  return { ...state, search: next };
}

function reduceSearch(state: HoneyState, event: HoneyEvent): HoneyState {
  switch (event.type) {
    case 'search':
      return event.path === state.path ? { ...state, search: pending(event.q) } : state;
    case 'found':
      return found(state, event);
    case 'search_failed':
      return state.search && event.path === state.path && event.q === state.search.q
        ? { ...state, search: { ...state.search, done: true, error: event.error } }
        : state;
    case 'clear_search':
      return state.search ? { ...state, search: null } : state;
    default:
      return state;
  }
}

export function reduceHoney(state: HoneyState, event: HoneyEvent): HoneyState {
  // Each family of events is handled apart; one that is not its own returns the state untouched.
  return reduceSearch(reduceReading(reduceFolder(state, event), event), event);
}
