"""Read one document at a Honey browser path, showing a reader only what it may see.

A document is one item in the Honey browser's folder tree: a Honey row (ripened knowledge in the
Honey Store, the Hive's cold tier), a live Cell Wax note (a Queen-written caution about one Cell)
or a Bee Bread entry (the warm memory tier). `read_document` resolves one, and the visibility
rules beside it decide, for every kind, whether a reader may see an item at all: the same two
tests retrieval applies -- a `honey:read` capability whose glob matches the item's scope, and a
label at or below the reader's clearance ceiling -- plus liveness (a Honey row neither tainted nor
retired, a wax note not past its expiry). An item that fails any test is reported exactly as a
missing one, so a reader cannot probe for what it may not read.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Called by `hivemind.honey_store.browse.browser` (`cat`), `.folders` (the
    wax listing, a document path given to `ls`) and `.relabel` (finding the row to relabel).
    Calls into this package's own store, scope, models and retrieval reader, and its `paths`,
    `sources` and `errors` only.

Key invariants:
    - Every visibility test here is the retrieval filter restated per item: scope by
      `hivemind.honey_store.scope.is_readable` (guard's own glob matching), clearance by rank.
    - A Bee Bread entry's scope is what ripening would file it under (`task:<id>` for an entry
      about a task, `hive` otherwise, ADR-0031's scoping table), so browsing it before it ripens
      is filtered exactly as querying it after.
    - Reading never writes anything, not even a trail event.

See Also:
    - hivemind.honey_store.honey.retrieve for the retrieval filter these tests restate.
    - hivemind.honey_store.scope.scope_for_nectar for the BEE_BREAD row `bee_bread_scope` mirrors.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.errors import BrowseNotFoundError, BrowsePathError
from hivemind.honey_store.browse.paths import CELL_SCOPE_PREFIX, BrowsePath, PathKind
from hivemind.honey_store.browse.sources import BeeBreadNote, BrowserDeps, WaxNote
from hivemind.honey_store.errors import HoneyNotFoundError
from hivemind.honey_store.honey import HoneyReader
from hivemind.honey_store.models import Honey
from hivemind.honey_store.scope import HIVE_SCOPE, cell_scope, is_readable, task_scope
from hivemind.honey_store.store import HoneyStore
from waggle.ids import CellId, HoneyId

# One document, whichever folder it lives in: a Honey row, a live wax note or a Bee Bread entry.
type BrowseDocument = Honey | WaxNote | BeeBreadNote

__all__ = [
    "BrowseDocument",
    "bee_bread_scope",
    "bee_bread_visible",
    "honey_visible",
    "live_wax_notes",
    "read_document",
    "visible_honey",
    "wax_visible",
]


async def read_document(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader
) -> BrowseDocument:
    """Return the one document at `target`, if `reader` may see it.

    Args:
        deps: The store and sources to read from, and the clock wax expiry is judged by.
        target: A parsed document path (HONEY, WAX_NOTE or BEE_BREAD_ENTRY).
        reader: Who is reading: its `honey:read` capabilities and clearance ceiling.

    Returns:
        The Honey row, wax note or Bee Bread entry at `target`.

    Raises:
        BrowsePathError: `target` is a folder, not a document.
        BrowseNotFoundError: Nothing at `target` is visible to `reader` (missing, hidden by scope
            or clearance, or no longer live: indistinguishable on purpose).
    """
    read = _READERS.get(target.kind)
    # A folder has contents, not a body: `ls` lists it.
    if read is None:
        raise BrowsePathError(target.path, "it is a folder; list it with ls")
    return await read(deps, target, reader)


def honey_visible(honey: Honey, reader: HoneyReader) -> bool:
    """Decide whether `reader` may see one Honey row, exactly as retrieval's filter would.

    Args:
        honey: The row.
        reader: Its `honey:read` capabilities and clearance ceiling.

    Returns:
        True when the row is live (neither tainted nor retired), its scope is readable and its
        label is at or below the ceiling.
    """
    # A tainted row (phase 10's audit) or a retired one is never returned, whatever its label.
    is_live = not honey.tainted and honey.retired_at is None
    return is_live and _allowed(honey.scope, honey.clearance, reader)


def wax_visible(note: WaxNote, reader: HoneyReader, now: datetime) -> bool:
    """Decide whether `reader` may see one live Cell Wax note right now.

    Args:
        note: A note its source reported as WRITTEN.
        reader: Its `honey:read` capabilities and clearance ceiling.
        now: The browser clock's current time.

    Returns:
        True when the note has not expired, its Cell's scope is readable and its label is at or
        below the ceiling.
    """
    # WRITTEN wax past its own expiry stays in the table until the House Bee's sweep expires it;
    # it is no longer live, so it is not shown.
    is_live = note.expires_at is None or note.expires_at > now
    return is_live and _allowed(cell_scope(note.cell_id), note.clearance, reader)


def bee_bread_scope(note: BeeBreadNote) -> str:
    """Return the scope a Bee Bread entry is filed under for reading.

    Args:
        note: The entry.

    Returns:
        `task:<id>` for an entry about a task, else `hive`: the scope its ripened Honey would
        carry (ADR-0031's scoping table, BEE_BREAD row), so it reads the same before and after.
    """
    return task_scope(note.task_id) if note.task_id is not None else HIVE_SCOPE


def bee_bread_visible(note: BeeBreadNote, reader: HoneyReader) -> bool:
    """Decide whether `reader` may see one Bee Bread entry.

    Args:
        note: The entry.
        reader: Its `honey:read` capabilities and clearance ceiling.

    Returns:
        True when the entry's scope is readable and its label is at or below the ceiling.
    """
    return _allowed(bee_bread_scope(note), note.clearance, reader)


async def live_wax_notes(deps: BrowserDeps, scope: str, reader: HoneyReader) -> tuple[WaxNote, ...]:
    """Return one Cell's live Cell Wax that `reader` may see, newest first.

    Args:
        deps: The wax source and the clock expiry is judged by.
        scope: The Cell's scope, `cell:<id>`.
        reader: Its `honey:read` capabilities and clearance ceiling.

    Returns:
        The Cell's live notes visible to `reader`; empty when its scope is not readable at all.
    """
    # A reader that may not read the Cell's scope sees none of its folder: skip the source.
    if not is_readable(scope, reader.capabilities):
        return ()
    cell_id = CellId(scope.removeprefix(CELL_SCOPE_PREFIX))
    # The memory store's own read, on its own thread: milliseconds, like every store read.
    notes = await deps.sources.wax.live_wax(cell_id, reader.ceiling)
    now = deps.clock.now()
    # The source is trusted for nothing: the Cell, the label and the expiry are all rechecked.
    return tuple(
        note for note in notes if note.cell_id == cell_id and wax_visible(note, reader, now)
    )


async def visible_honey(store: HoneyStore, target: BrowsePath, reader: HoneyReader) -> Honey:
    """Return the Honey row a HONEY path names, if `reader` may see it.

    Args:
        store: The Honey Store holding the row.
        target: A parsed HONEY path.
        reader: Its `honey:read` capabilities and clearance ceiling.

    Returns:
        The row, live, readable and within the ceiling.

    Raises:
        BrowseNotFoundError: No such row, or one `reader` may not see, or one filed under a
            different scope than the path's folder (indistinguishable on purpose).
    """
    try:
        # Local SQLite on the store's own thread: milliseconds.
        honey = await store.get_honey(HoneyId(target.item_id or ""))
    except HoneyNotFoundError as exc:
        raise BrowseNotFoundError(target.path) from exc
    # A row reached through another scope's folder is not at this path: its path is its scope's.
    if honey.scope != target.scope or not honey_visible(honey, reader):
        raise BrowseNotFoundError(target.path)
    return honey


def _allowed(scope: str, clearance: HoneyClearance, reader: HoneyReader) -> bool:
    """Apply retrieval's two tests to one item: a readable scope, a label within the ceiling."""
    return is_readable(scope, reader.capabilities) and clearance.rank <= reader.ceiling.rank


async def _read_honey(deps: BrowserDeps, target: BrowsePath, reader: HoneyReader) -> Honey:
    """Return the Honey row a HONEY path names, if the reader may see it."""
    return await visible_honey(deps.store, target, reader)


async def _read_wax_note(deps: BrowserDeps, target: BrowsePath, reader: HoneyReader) -> WaxNote:
    """Return the live wax note a WAX_NOTE path names, if the reader may see it."""
    # Found among the Cell's own visible live notes, so `cat` shows exactly what `ls` lists.
    notes = await live_wax_notes(deps, target.scope or "", reader)
    for note in notes:
        if note.id == target.item_id:
            return note
    raise BrowseNotFoundError(target.path)


async def _read_bee_bread(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader
) -> BeeBreadNote:
    """Return the Bee Bread entry a BEE_BREAD_ENTRY path names, if the reader may see it."""
    # The memory store's own lookup by id, on its own thread: milliseconds.
    note = await deps.sources.bee_bread.entry(target.item_id or "", reader.ceiling)
    # Missing, above the ceiling, or filed under a scope the reader may not read: all alike.
    if note is None or not bee_bread_visible(note, reader):
        raise BrowseNotFoundError(target.path)
    return note


# One reader per document kind; a kind missing here is a folder (8.4: a registry, not an if chain).
_READERS: dict[
    PathKind, Callable[[BrowserDeps, BrowsePath, HoneyReader], Awaitable[BrowseDocument]]
] = {
    PathKind.HONEY: _read_honey,
    PathKind.WAX_NOTE: _read_wax_note,
    PathKind.BEE_BREAD_ENTRY: _read_bee_bread,
}
