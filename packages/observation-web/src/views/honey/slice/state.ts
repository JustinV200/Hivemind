/**
 * The Honey slice's state and events: the open folder, the open item and the folder's search.
 *
 * The Honey browser reads three routes (a folder, an item, a search) and has no stream of its own,
 * so everything the view shows is held here as the generated types arrived, plus the little the
 * view must remember: which folder is open, whether the list was cut by the bound, which item the
 * reading pane shows and what the folder's search has found so far.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/slice/`. Imported by the reducer, the fold, the
 *   hooks and the view's components.
 *
 * Key invariants:
 *   - Types and constants only; nothing here runs.
 *   - Every record is a generated schema type; no Landing Board shape is written by hand.
 *   - Paths are held normalised, with a leading slash (`/`, `/hive`, `/cells/<id>`).
 *
 * See Also:
 *   - src/views/honey/slice/reduce.ts for how events change the state.
 *   - docs/observation/design.md section 5.7.
 */
import type { components } from '../../../landing_board/types';

type Schemas = components['schemas'];
export type HoneyFolder = Schemas['HoneyFolderView'];
export type FolderEntry = Schemas['HoneyFolderEntryView'];
export type ItemSummary = Schemas['HoneyItemSummary'];
export type ItemView = Schemas['HoneyItemView'];
export type ProvenanceView = Schemas['HoneyProvenanceView'];
export type SearchHit = Schemas['HoneySearchHit'];
export type SearchPage = Schemas['HoneySearchPage'];
export type TrailFrame = Schemas['TrailFrame'];
export type TrailEvent = Schemas['TrailEventView'];

// A folder of tens of thousands of rows is not read by paging to its end; past this the list is cut.
export const MAX_ITEMS = 2000;
// A search past a few pages is a search to refine, so hits are bounded much lower than a folder.
export const MAX_HITS = 500;

export interface ReadingState {
  id: string;
  // Null until the item's read lands.
  item: ItemView | null;
  error: unknown;
}

export interface SearchState {
  q: string;
  hits: readonly SearchHit[];
  nextCursor: string | null;
  // The page's own explanation (for example that the search is exhausted); empty when none.
  note: string;
  // False while the first page is in flight, so "No matches" is never shown too early.
  done: boolean;
  truncated: boolean;
  error: unknown;
}

export interface HoneyState {
  // The folder the reader opened; null until a view opens one.
  path: string | null;
  folder: HoneyFolder | null;
  // True once the bound dropped the tail of the list, so "More" is no longer offered.
  truncated: boolean;
  // The last refusal or failure of a folder read, shown through `ErrorNotice`; null when well.
  error: unknown;
  // How many trail events concerned this folder since it was opened; the hook re-reads on a move.
  trailTicks: number;
  reading: ReadingState | null;
  search: SearchState | null;
}

export type HoneyEvent =
  | { type: 'open'; path: string }
  | { type: 'seeded'; path: string; folder: HoneyFolder }
  | { type: 'more'; path: string; cursor: string; folder: HoneyFolder }
  | { type: 'failed'; path: string; error: unknown }
  | { type: 'trail'; frame: TrailFrame }
  | { type: 'read_item'; id: string }
  | { type: 'item'; id: string; item: ItemView }
  | { type: 'item_failed'; id: string; error: unknown }
  | { type: 'close_item' }
  | { type: 'search'; path: string; q: string }
  | { type: 'found'; path: string; q: string; cursor: string | null; page: SearchPage }
  | { type: 'search_failed'; path: string; q: string; error: unknown }
  | { type: 'clear_search' };

export const initialHoney: HoneyState = {
  path: null,
  folder: null,
  truncated: false,
  error: null,
  trailTicks: 0,
  reading: null,
  search: null,
};
