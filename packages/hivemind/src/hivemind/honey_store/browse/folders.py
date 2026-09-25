"""List one Honey browser folder for a reader, filtered by its scope and clearance like retrieval.

The Honey browser presents the Honey Store (the Hive's cold-tier knowledge base) as a read-only
folder tree. `list_folder` answers `ls` for every folder kind: the root's five folders; `/cells`,
`/bees` and `/tasks`, one folder per scope holding at least one live row the reader may see (and,
under `/cells`, every Cell with live wax it may see, so a Cell with no Honey yet is still found);
a scope's own Honey rows (a Cell's folder also offers its `wax` sub-folder); a Cell's live Cell
Wax (the Queen's standing cautions about it); recent Bee Bread (the warm memory tier). Every read
goes through the reader's own `ReadFilter` or the visibility rules in `.documents`, so a listing
never shows what a query by the same reader could not return. The three index folders are derived
from `HoneyStore.scope_counts`'s own `GROUP BY` (ADR-0037), so they are complete at any store
size, with no scan to bound; `search_scopes` reuses the same read, so a search in `/cells` covers
every listed folder that holds Honey.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Called by `hivemind.honey_store.browse.browser` (`ls` and `search`).
    Calls into this package's own store protocol, scope and retrieval reader, and its `paths`,
    `listing`, `documents`, `sources` and `errors` only.

Key invariants:
    - Filtering is policy, not paging (ADR-0035): the store applies the reader's filter before it
      limits a page, and the wax and Bee Bread listings filter before they page.
    - A folder whose scope the reader may not read lists as empty, never as an error, so an empty
      folder and a forbidden one look the same.
    - Listing never writes anything, not even a trail event.

See Also:
    - hivemind.honey_store.store.protocol.HoneyStore.list_honey and .scope_counts for the paged
      and grouped reads used here.
    - hivemind.honey_store.browse.documents for the per-item visibility rules.
    - docs/adr/0037-honey-keeps-repeat-sources-lists-scopes-and-prunes-on-request.md for the
      scope_counts read that replaced the bounded scan.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from hivemind.honey_store.browse.documents import (
    bee_bread_visible,
    live_wax_notes,
    read_document,
    wax_visible,
)
from hivemind.honey_store.browse.errors import BrowseInputError, BrowsePathError
from hivemind.honey_store.browse.listing import (
    BrowseEntry,
    BrowseListing,
    bee_bread_entry,
    folder_entry,
    honey_entry,
    wax_entry,
)
from hivemind.honey_store.browse.paths import (
    CELL_SCOPE_KIND,
    CELL_SCOPE_PREFIX,
    ROOT_PATH,
    TOP_FOLDERS,
    BrowsePath,
    PathKind,
    wax_folder_path,
)
from hivemind.honey_store.browse.sources import BeeBreadNote, BrowserDeps, WaxNote
from hivemind.honey_store.honey import HoneyReader
from hivemind.honey_store.models import Honey, ReadFilter
from hivemind.honey_store.scope import cell_scope, folder_for_scope, is_readable, readable_globs
from hivemind.honey_store.store import HoneyStore

DEFAULT_PAGE_ROWS = 50  # A screenful: enough to see a folder's shape, short enough to read.
MAX_PAGE_ROWS = 500  # One page never carries more rows than a reader could scan by eye.
RECENT_BEE_BREAD = timedelta(days=7)  # "Recent" warm memory: older entries have ripened by now.
MAX_BEE_BREAD_ROWS = 1_000  # Newest entries fetched per listing, before the scope filter pages.
# What the root lists under each of its five folders, in TOP_FOLDERS order.
_TOP_FOLDER_TITLES = (
    "Shared knowledge: findings and verified task outcomes",
    "One folder per Cell with Honey: its history and its live Cell Wax",
    "One folder per bee with Honey of its own",
    "One folder per task with working material: transcripts, tool results, Handoffs",
    "Recent Bee Bread, the warm memory tier",
)
_WAX_FOLDER_TITLE = "This Cell's live Cell Wax: the Queen's standing cautions"

__all__ = [
    "DEFAULT_PAGE_ROWS",
    "FIRST_PAGE",
    "MAX_BEE_BREAD_ROWS",
    "MAX_PAGE_ROWS",
    "RECENT_BEE_BREAD",
    "Page",
    "list_folder",
    "reader_filter",
    "scan_scopes",
    "search_scopes",
]


@dataclass(frozen=True, slots=True)
class Page:
    """Which page of a folder to list: at most `limit` entries, after skipping `offset`."""

    limit: int = DEFAULT_PAGE_ROWS  # Entries on this page; 1 to MAX_PAGE_ROWS.
    offset: int = 0  # Entries skipped before this page; 0 or more.

    def __post_init__(self) -> None:
        """Refuse a page no listing could serve, before any store is read."""
        if not 1 <= self.limit <= MAX_PAGE_ROWS:
            raise BrowseInputError("page limit", f"it must be 1 to {MAX_PAGE_ROWS}")
        if self.offset < 0:
            raise BrowseInputError("page offset", "it must be 0 or more")


# The first page of any folder, the default every `ls` starts from.
FIRST_PAGE = Page()


async def list_folder(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader, page: Page
) -> BrowseListing:
    """List one folder's contents for `reader`, one page at a time.

    Args:
        deps: The store and sources to read from.
        target: The parsed path; a document path lists as that one document.
        reader: Its `honey:read` capabilities and clearance ceiling.
        page: Which page to return.

    Returns:
        The page, and whether more exists beyond it.

    Raises:
        BrowseNotFoundError: `target` is a document path with nothing visible at it.
    """
    lister = _LISTERS.get(target.kind)
    # Like `ls file` on a filesystem: a document path lists as the one document it names.
    if lister is None:
        document = await read_document(deps, target, reader)
        return BrowseListing(
            path=target.path, entries=(_document_entry(document),), is_truncated=False
        )
    return await lister(deps, target, reader, page)


def reader_filter(reader: HoneyReader, requested: tuple[str, ...] = ()) -> ReadFilter:
    """Build the store filter for `reader`: exactly the one retrieval builds for the same reader.

    Args:
        reader: Its `honey:read` capabilities and clearance ceiling.
        requested: Exact scopes to narrow to; empty means every readable scope.

    Returns:
        The ReadFilter the store applies before ranking, paging or limiting anything.
    """
    return ReadFilter(
        readable=readable_globs(reader.capabilities),
        max_clearance=reader.ceiling,
        requested=requested,
    )


async def scan_scopes(store: HoneyStore, reader: HoneyReader, scope_kind: str) -> dict[str, int]:
    """Find the scopes of one kind holding at least one live row `reader` may see.

    Args:
        store: The Honey Store.
        reader: Its `honey:read` capabilities and clearance ceiling.
        scope_kind: "cell", "bee" or "task".

    Returns:
        Each such scope mapped to how many visible rows it holds; complete at any store size
        (`HoneyStore.scope_counts`'s own `GROUP BY`, under the reader's own filter, ADR-0037).
    """
    return await store.scope_counts(scope_kind, reader_filter(reader))


async def search_scopes(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader
) -> tuple[str, ...] | None:
    """Return the scopes a search in `target` covers.

    Args:
        deps: The store to count an index folder's scopes with.
        target: The folder searched.
        reader: Its `honey:read` capabilities and clearance ceiling.

    Returns:
        An empty tuple for the root (every scope the reader may read), the folder's own scope
        for a scope folder, the scopes `ls` lists with visible Honey for an index folder, or None
        when no such scope exists (a search there must find nothing, never everything).

    Raises:
        BrowsePathError: `target` holds no Honey to search: a document, a Cell's live wax, or
            Bee Bread, which is lookup-only (codingrules 8.9).
    """
    if target.kind is PathKind.ROOT:
        return ()
    if target.kind is PathKind.SCOPE and target.scope is not None:
        return (target.scope,)
    if target.kind is PathKind.SCOPE_INDEX and target.scope_kind is not None:
        counts = await scan_scopes(deps.store, reader, target.scope_kind)
        return tuple(sorted(counts)) or None
    raise BrowsePathError(
        target.path, "only a folder that holds Honey can be searched; wax and Bee Bread cannot"
    )


async def _list_root(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader, page: Page
) -> BrowseListing:
    """List the five top-level folders; the same for every reader, since they reveal nothing."""
    entries = tuple(
        folder_entry(f"{ROOT_PATH}{name}", title)
        for name, title in zip(TOP_FOLDERS, _TOP_FOLDER_TITLES, strict=True)
    )
    return _paged(target.path, entries, page)


async def _list_index(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader, page: Page
) -> BrowseListing:
    """List `/cells`, `/bees` or `/tasks`: one folder per scope with something the reader sees."""
    scope_kind = target.scope_kind or ""
    counts = await scan_scopes(deps.store, reader, scope_kind)
    # A Cell whose only content so far is its live wax still has a folder worth walking into.
    wax = await _wax_counts(deps, reader) if scope_kind == CELL_SCOPE_KIND else {}
    entries = tuple(
        folder_entry(
            folder_for_scope(scope), scope, _folder_detail(counts.get(scope, 0), wax.get(scope, 0))
        )
        for scope in sorted(counts.keys() | wax.keys())
    )
    # scope_counts' own GROUP BY has no scan bound to stop at (ADR-0037), so this listing is
    # always complete: only the page cut below can ever leave more entries beyond it.
    return _paged(target.path, entries, page)


async def _list_scope(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader, page: Page
) -> BrowseListing:
    """List one scope's Honey rows, newest first; a Cell's folder also offers its wax folder."""
    scope = target.scope or ""
    # A scope the reader may not read lists as empty: the same answer an empty folder gives.
    if not is_readable(scope, reader.capabilities):
        return BrowseListing(path=target.path, entries=(), is_truncated=False)
    # One row past the page tells whether another page exists. Local SQLite: milliseconds.
    rows = await deps.store.list_honey(
        reader_filter(reader, (scope,)),
        scope_prefix=None,
        limit=page.limit + 1,
        offset=page.offset,
    )
    entries = tuple(honey_entry(honey) for honey in rows[: page.limit])
    # The wax sub-folder heads a Cell's first page; it is not a row, so it is not counted.
    if scope.startswith(CELL_SCOPE_PREFIX) and page.offset == 0:
        entries = (folder_entry(wax_folder_path(scope), _WAX_FOLDER_TITLE), *entries)
    return BrowseListing(path=target.path, entries=entries, is_truncated=len(rows) > page.limit)


async def _list_wax(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader, page: Page
) -> BrowseListing:
    """List one Cell's live Cell Wax the reader may see, newest first."""
    notes = await live_wax_notes(deps, target.scope or "", reader)
    return _paged(target.path, tuple(wax_entry(note) for note in notes), page)


async def _list_bee_bread(
    deps: BrowserDeps, target: BrowsePath, reader: HoneyReader, page: Page
) -> BrowseListing:
    """List recent Bee Bread the reader may see, newest first."""
    now = deps.clock.now()
    # The memory store's own time-range read, on its own thread: milliseconds.
    notes = await deps.sources.bee_bread.recent(
        now - RECENT_BEE_BREAD, now, reader.ceiling, MAX_BEE_BREAD_ROWS
    )
    # Filter before paging: a page never comes up short because hidden entries took its rows.
    visible = tuple(bee_bread_entry(note) for note in notes if bee_bread_visible(note, reader))
    listing = _paged(target.path, visible, page)
    # Fewer than the bound came back: the whole week was read, nothing lies beyond it.
    if len(notes) < MAX_BEE_BREAD_ROWS:
        return listing
    # The fetch hit its bound: older entries of the week may lie beyond what was read.
    note = f"Only the newest {MAX_BEE_BREAD_ROWS} entries of the last week were read."
    return listing.model_copy(update={"is_truncated": True, "note": note})


async def _wax_counts(deps: BrowserDeps, reader: HoneyReader) -> dict[str, int]:
    """Count every Cell's live wax the reader may see, by the Cell's scope."""
    # One read of every Cell's WRITTEN wax; the memory store's own thread, milliseconds.
    notes = await deps.sources.wax.live_wax(None, reader.ceiling)
    now = deps.clock.now()
    counts: dict[str, int] = {}
    # Only notes the reader may see, and still live, make a Cell's folder worth listing.
    for note in notes:
        if wax_visible(note, reader, now):
            scope = cell_scope(note.cell_id)
            counts[scope] = counts.get(scope, 0) + 1
    return counts


def _folder_detail(rows: int, wax: int) -> str:
    """Say what one scope folder holds: its visible Honey rows, and any live wax."""
    parts = [f"{rows} rows"] if rows else []
    if wax:
        parts.append(f"{wax} wax")
    return ", ".join(parts)


def _paged(path: str, entries: tuple[BrowseEntry, ...], page: Page) -> BrowseListing:
    """Cut an already-filtered, fully-built listing down to one page."""
    end = page.offset + page.limit
    return BrowseListing(
        path=path, entries=entries[page.offset : end], is_truncated=len(entries) > end
    )


def _document_entry(document: Honey | WaxNote | BeeBreadNote) -> BrowseEntry:
    """Build the one entry a document path lists as."""
    if isinstance(document, Honey):
        return honey_entry(document)
    if isinstance(document, WaxNote):
        return wax_entry(document)
    return bee_bread_entry(document)


# One lister per folder kind; a kind missing here is a document (8.4: a registry, not an if chain).
_LISTERS: dict[
    PathKind, Callable[[BrowserDeps, BrowsePath, HoneyReader, Page], Awaitable[BrowseListing]]
] = {
    PathKind.ROOT: _list_root,
    PathKind.SCOPE_INDEX: _list_index,
    PathKind.SCOPE: _list_scope,
    PathKind.WAX_FOLDER: _list_wax,
    PathKind.BEE_BREAD: _list_bee_bread,
}
