/**
 * The pure folds of the Honey slice: merging a page into a list and deciding what a trail event is.
 *
 * Folder pages carry sub-folders and items together, so a later page can add either, and a
 * re-read after a trail event can repeat what is already held. The merge keeps each folder by
 * path and each item by id, first sighting wins, and cuts at the bound. `concerns` is the rule that
 * says which trail events make a folder worth reading again: a Cell's folder only for Honey or Cell
 * Wax events that name that Cell, every other folder for any Honey event.
 *
 * Fits into the Hive:
 *   Layer 7 (observation-web), `src/views/honey/slice/`. Called by `reduce.ts`; no state of its own.
 *
 * Key invariants:
 *   - Pure: same inputs, same outputs; no clock, no I/O.
 *   - A merge never reorders what is already held; new entries go after it.
 *   - A Cell's wax folder (`/cells/<id>/wax`) counts as that Cell's folder.
 *
 * See Also:
 *   - packages/hivemind/src/hivemind/pheromone/events/families/ for the `honey.*` and
 *     `memory.wax_*` kinds (a wax event's subject is its Cell).
 *   - src/views/honey/hooks/use_folder.ts, which turns a moved counter into one read.
 */
import { MAX_HITS, MAX_ITEMS } from './state';
import type { HoneyFolder, SearchHit, TrailEvent } from './state';

// `list` then `extra`, each key once (the first sighting wins), cut to `bound`.
export function joinBy<T>(
  list: readonly T[],
  extra: readonly T[],
  keyOf: (entry: T) => string,
  bound: number,
): { merged: T[]; cut: boolean } {
  const seen = new Set<string>();
  const all = [...list, ...extra].filter((entry) => {
    const key = keyOf(entry);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return { merged: all.slice(0, bound), cut: all.length > bound };
}

// A folder page after `held`: the held folder's header is replaced by the newer page's.
export function appendPage(
  held: HoneyFolder,
  page: HoneyFolder,
): { folder: HoneyFolder; cut: boolean } {
  const items = joinBy(held.items, page.items, (item) => item.id, MAX_ITEMS);
  const folders = joinBy(held.folders, page.folders, (folder) => folder.path, MAX_ITEMS);
  // A cut list cannot be continued honestly: the cursor would skip the rows that were dropped.
  const cut = items.cut || folders.cut;
  return {
    folder: {
      ...page,
      items: items.merged,
      folders: folders.merged,
      next_cursor: cut ? null : page.next_cursor,
    },
    cut,
  };
}

export function appendHits(
  held: readonly SearchHit[],
  more: readonly SearchHit[],
): { hits: SearchHit[]; cut: boolean } {
  const { merged, cut } = joinBy(held, more, (hit) => hit.id, MAX_HITS);
  return { hits: merged, cut };
}

// The Cell a folder path belongs to (`/cells/<id>` and below), or null for every other folder.
export function cellOf(path: string): string | null {
  const [head, id] = path.split('/').filter((segment) => segment !== '');
  return head === 'cells' && id !== undefined ? id : null;
}

// True when the event names the Cell as its subject, its actor or its payload's `cell_id`.
function names(event: TrailEvent, cell: string): boolean {
  return [event.subject_id, event.actor, event.payload['cell_id']].some((id) => id === cell);
}

export function concerns(path: string, event: TrailEvent): boolean {
  const honey = event.kind.startsWith('honey.');
  const cell = cellOf(path);
  // Any other folder is read again for any Honey event: the store changed somewhere under it.
  if (cell === null) return honey;
  // A Cell's folder holds its Honey and its live Cell Wax, so either kind counts, if it names it.
  const wax = event.kind.startsWith('memory.wax_');
  return (honey || wax) && names(event, cell);
}
