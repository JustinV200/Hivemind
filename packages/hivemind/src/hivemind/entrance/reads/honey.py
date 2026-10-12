"""Gather the Honey browser's reads for one viewer: their reader, their folders, items and search.

The Honey browser (roadmap step 12.9) is a view over provenance, filtered by the viewer's
``observe:honey:<scope>`` capabilities and clearance allowance (codingrules 8.11). The browser in
``hivemind.honey_store.browse`` already filters by a ``HoneyReader``: the ``honey:read`` scopes it
holds and the most sensitive label it may see. This module is where a device becomes one. Each
``observe:honey:<scope>`` the device holds becomes the ``honey:read:<scope>`` glob that matches the
same scopes (a prefix scope ending in ``*`` is the glob it already looks like), and its
allowance is the highest ``honey:clearance`` rung it holds, ``C1`` when it holds none, since
``observe`` alone is the Hive's non-personal tier everywhere else. A scope with anything in it a
glob would read differently from a prefix is left out, so a strange grant can only narrow the
viewer, never widen it. The routes check the folder's own scope at the gate; here the lists are
cut to the same scopes, so a folder the viewer may not read is not listed in its parent either.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads``. Called by
    ``hivemind.entrance.routes.hive.honey``. Calls into the Honey browser through ``HoneyReads``,
    the capability set the session holds and the Honey view builders.

Key invariants:
    - Read only. A viewer never sees an item outside its scopes or above its allowance: the
      browser filters, and the privileged read (``locate``) returns a label and a scope, never
      the document.
    - A device holding no ``observe:honey`` capability is a viewer of nothing: an empty root.
    - The privileged reader is used for one thing, to learn an item's scope and label.

See Also:
    - hivemind.honey_store.browse for the tree, the listing and the visibility rules.
    - hivemind.observation.views.honey for the views built here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hivemind.cell import HoneyClearance
from hivemind.entrance.errors import (
    HoneyFolderNotFoundError,
    HoneyItemNotFoundError,
    HoneyUnavailableError,
)
from hivemind.entrance.gate.reads import HiveReads, HoneyReads
from hivemind.guard import Capability, CapabilityFamily, CapabilitySet
from hivemind.honey_store.browse import (
    EMPTY_INDEX_REASON,
    MAX_PAGE_ROWS,
    ROOT_PATH,
    BrowseDocument,
    BrowseError,
    BrowseListing,
    BrowseNotFoundError,
    BrowsePath,
    BrowsePathError,
    Page,
    PathKind,
    WaxNote,
    bee_bread_scope,
    parse_path,
)
from hivemind.honey_store.honey import HoneyReader
from hivemind.honey_store.models import Honey
from hivemind.honey_store.scope import (
    HIVE_SCOPE,
    cell_scope,
    is_readable,
    queen_read_capabilities,
    readable_globs,
)
from hivemind.observation import (
    HoneyFolderView,
    HoneyItemView,
    HoneySearchPage,
    HoneySearchQuery,
    HoneyTreeQuery,
)
from waggle.messages.honey.exchange import MAX_MAX_HITS

# A held scope the viewer's glob may carry as it is: scope characters, colons, and at most one
# trailing '*'. Anything else ('?', '[', a '*' inside) means something else to a glob than to a
# prefix match, so that capability is left out rather than read two ways.
_PLAIN_SCOPE = re.compile(r"^[A-Za-z0-9_:\-]+\*?$|^\*$")
_BASELINE = HoneyClearance.C1  # What `observe` alone may read of Honey: internal, not personal.
_RUNGS = (HoneyClearance.C2, HoneyClearance.C1, HoneyClearance.C0)  # Highest first.
# Each index folder, and the scope prefix its Cells', bees' or tasks' folders are filed under.
_INDEX_PREFIXES = {"/cells": "cell:", "/bees": "bee:", "/tasks": "task:"}
_HIVE_FOLDER = "/hive"
_BEE_BREAD_FOLDER = "/bee-bread"
CAP_NOTE = (
    f"A search ranks at most {MAX_MAX_HITS} hits; narrow the words or the folder to see more."
)

__all__ = [
    "HoneyViewer",
    "honey_viewer",
    "locate_folder",
    "locate_item",
    "read_folder",
    "read_item",
    "read_search",
    "require_honey",
    "scope_of",
]


@dataclass(frozen=True, slots=True)
class HoneyViewer:
    """One device as a reader of Honey: the reader the browser filters by, and what it shows."""

    reader: HoneyReader  # Its honey:read scopes and clearance ceiling.

    @property
    def ceiling(self) -> HoneyClearance:
        """The most sensitive label this viewer may read."""
        return self.reader.ceiling

    def shows(self, folder: str) -> bool:
        """Say whether a top-level folder belongs in this viewer's root.

        Args:
            folder: One of the root's five folders.

        Returns:
            True when some scope the viewer may read lives under it.
        """
        caps = self.reader.capabilities
        if folder == _HIVE_FOLDER:
            return is_readable(HIVE_SCOPE, caps)
        globs = readable_globs(caps)
        if folder == _BEE_BREAD_FOLDER:
            # Bee Bread entries are filed under the Hive or a task.
            return is_readable(HIVE_SCOPE, caps) or _reaches(globs, "task:")
        return _reaches(globs, _INDEX_PREFIXES[folder])


def honey_viewer(device_id: str, held: CapabilitySet) -> HoneyViewer:
    """Make a device the reader of Honey its capabilities describe.

    Args:
        device_id: The device, the subject the retriever records its query under.
        held: The session's capabilities.

    Returns:
        The viewer: each ``observe:honey:<scope>`` as the matching ``honey:read`` scope, and the
        highest ``honey:clearance`` rung as the ceiling (``C1`` when it holds none).
    """
    scopes = sorted(
        {
            capability.scope
            for capability in held
            if capability.family is CapabilityFamily.OBSERVE_HONEY
            and _PLAIN_SCOPE.fullmatch(capability.scope)
        }
    )
    reads = CapabilitySet.parse(*(f"{CapabilityFamily.HONEY_READ.value}:{s}" for s in scopes))
    return HoneyViewer(
        HoneyReader(
            requester=device_id, capabilities=reads, ceiling=_ceiling(held), is_night_veil=False
        )
    )


def require_honey(reads: HiveReads) -> HoneyReads:
    """Return the Honey browser, or refuse on a Hive that keeps no Honey Store.

    Args:
        reads: The stores the Entrance reads.

    Returns:
        The browser's read side.

    Raises:
        HoneyUnavailableError: The Hive has no Honey Store (404).
    """
    if reads.honey is None:
        raise HoneyUnavailableError()
    return reads.honey


def locate_folder(path: str) -> BrowsePath:
    """Parse a path the caller named as a folder.

    Args:
        path: The path as the caller gave it.

    Returns:
        What it names; a scope folder or a Cell's wax folder carries its scope.

    Raises:
        HoneyFolderNotFoundError: The path is outside the tree, or names one document (404).
    """
    try:
        target = parse_path(path)
    except BrowsePathError as exc:
        raise HoneyFolderNotFoundError(path) from exc
    if not target.is_folder:
        raise HoneyFolderNotFoundError(path)
    return target


async def read_folder(
    honey: HoneyReads, viewer: HoneyViewer, target: BrowsePath, query: HoneyTreeQuery
) -> HoneyFolderView:
    """Read one page of a folder the viewer's scopes already reach.

    Args:
        honey: The Honey browser's read side.
        viewer: Who reads.
        target: The folder, checked by the route against the viewer's scope.
        query: The page: where to continue from, how many.

    Returns:
        The folder as the viewer may see it.
    """
    offset = query.offset
    if target.kind is PathKind.ROOT:
        listing = await _root(honey, viewer, Page(limit=query.limit, offset=offset))
    else:
        # Local reads (SQLite, and memory for wax and Bee Bread), cut to the viewer's scopes.
        listing = await honey.ls(target.path, viewer.reader, Page(query.limit, offset))
    return HoneyFolderView.of_listing(listing, viewer.ceiling, offset, query.limit)


async def locate_item(honey: HoneyReads, device_id: str, item_id: str) -> BrowseDocument:
    """Find an item by its id to learn its scope and label, whatever the viewer may see.

    The document returned is for the route to judge and drop; it never reaches a client.

    Args:
        honey: The Honey browser's read side.
        device_id: The device asking, the subject any record names.
        item_id: The item's id.

    Returns:
        The document with this id.

    Raises:
        HoneyItemNotFoundError: Nothing live has this id (404).
    """
    everything = HoneyReader(
        requester=device_id,
        capabilities=queen_read_capabilities(),
        ceiling=HoneyClearance.C2,
        is_night_veil=False,
    )
    try:
        return await honey.find(item_id, everything)
    except BrowseNotFoundError as exc:
        raise HoneyItemNotFoundError(item_id) from exc


def scope_of(document: BrowseDocument) -> tuple[str, HoneyClearance]:
    """Say where a document is filed and how sensitive it is.

    Args:
        document: A Honey row, a wax note or a Bee Bread entry.

    Returns:
        Its scope (what ``observe:honey:<scope>`` must cover) and its label.
    """
    if isinstance(document, Honey):
        return document.scope, document.clearance
    if isinstance(document, WaxNote):
        return cell_scope(document.cell_id), document.clearance
    return bee_bread_scope(document), document.clearance


async def read_item(honey: HoneyReads, viewer: HoneyViewer, item_id: str) -> HoneyItemView:
    """Read an item through the viewer's own reader, once the route has judged it readable.

    Args:
        honey: The Honey browser's read side.
        viewer: Who reads.
        item_id: The item's id.

    Returns:
        The item with its text.

    Raises:
        HoneyItemNotFoundError: The browser, filtering again for the viewer, shows nothing (404).
    """
    try:
        return HoneyItemView.of_document(await honey.find(item_id, viewer.reader))
    except BrowseNotFoundError as exc:
        raise HoneyItemNotFoundError(item_id) from exc


async def read_search(
    honey: HoneyReads, viewer: HoneyViewer, target: BrowsePath, query: HoneySearchQuery
) -> HoneySearchPage:
    """Search a folder the viewer's scopes already reach, one page of ranked hits.

    Args:
        honey: The Honey browser's read side.
        viewer: Who searches.
        target: The folder, checked by the route against the viewer's scope.
        query: The words, and the page.

    Returns:
        The page; the count of hits the viewer's scopes withheld is never shown.

    Raises:
        HoneyFolderNotFoundError: The folder holds no Honey to search (404).
        HoneyUnavailableError: The Honey browser was built without a retriever (404).
    """
    offset = query.offset
    # One hit past the page tells whether another exists; the retriever ranks 50 at most.
    window = min(offset + query.limit + 1, MAX_MAX_HITS)
    try:
        response = await honey.search(target.path, query.q, viewer.reader, window)
    except BrowsePathError as exc:
        raise HoneyFolderNotFoundError(target.path) from exc
    except BrowseError as exc:
        raise HoneyUnavailableError() from exc
    # The retriever's own reason counts what was withheld by scope or clearance: not for a viewer.
    note = EMPTY_INDEX_REASON if response.reason == EMPTY_INDEX_REASON else ""
    if len(response.hits) >= MAX_MAX_HITS and offset + query.limit >= MAX_MAX_HITS:
        note = CAP_NOTE
    return HoneySearchPage.of_hits(target.path, response.hits, offset, query.limit, note)


async def _root(honey: HoneyReads, viewer: HoneyViewer, page: Page) -> BrowseListing:
    """List the root's folders the viewer reaches: the browser lists five, this keeps its own."""
    whole = await honey.ls(ROOT_PATH, viewer.reader, Page(limit=MAX_PAGE_ROWS))
    shown = tuple(entry for entry in whole.entries if viewer.shows(entry.path))
    end = page.offset + page.limit
    return BrowseListing(
        path=whole.path, entries=shown[page.offset : end], is_truncated=len(shown) > end
    )


def _ceiling(held: CapabilitySet) -> HoneyClearance:
    """Return the highest ``honey:clearance`` rung held, or the ``observe`` baseline."""
    for rung in _RUNGS:
        needed = Capability(family=CapabilityFamily.HONEY_CLEARANCE, scope=rung.value.lower())
        if held.allows(needed):
            return rung
    return _BASELINE


def _reaches(globs: tuple[str, ...], prefix: str) -> bool:
    """Say whether some readable glob could match a scope that starts with ``prefix``."""
    for glob in globs:
        stem = glob.removesuffix("*")
        # A glob with a trailing '*' covers every scope that starts with its stem; an exact scope
        # covers just itself, so it reaches the prefix only when it starts with it.
        if glob.endswith("*") and (prefix.startswith(stem) or stem.startswith(prefix)):
            return True
        if not glob.endswith("*") and glob.startswith(prefix):
            return True
    return False
