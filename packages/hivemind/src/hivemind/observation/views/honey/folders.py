"""Define the Honey browser's folder models: a folder's sub-folders and a page of its items.

``HoneyFolderView`` is one folder of the Honey tree (``/``, ``/hive``, ``/cells/<id>``,
``/cells/<id>/wax``, ``/bees/<id>``, ``/tasks/<id>``, ``/bee-bread``): the folder's path and
parent, the scope it shows, whether it can be searched, the most sensitive label the viewer may
read, its sub-folders and one page of its items, and the cursor for the next page. It is built
from the browser's page of the folder, which the browser has already cut to the viewer's scopes
and clearance (``of_listing``), so a folder never lists what its viewer may not read.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.views.honey``.
    Built by ``hivemind.entrance.reads.honey``; published in the OpenAPI document. Calls into the
    Honey browser's listing, this package's item models and cursor, and pydantic.

Key invariants:
    - Pure: a listing is handed in and a view comes out.
    - Sub-folders and items are kept apart; only a folder that holds Honey is searchable.

See Also:
    - hivemind.honey_store.browse.folders for the listing this reshapes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse import BrowseEntry, BrowseListing, EntryKind, PathKind, parse_path
from hivemind.observation.views.honey.items import PATH_SEPARATOR, HoneyItemSummary, last_segment
from hivemind.observation.views.honey.queries import encode_honey_cursor

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.
# The folders that hold Honey rows, so `/v1/honey/search` can search them; wax and Bee Bread are
# lookup-only (codingrules 8.9).
_SEARCHABLE = (PathKind.ROOT, PathKind.SCOPE_INDEX, PathKind.SCOPE)

__all__ = ["HoneyFolderEntryView", "HoneyFolderView"]


class HoneyFolderEntryView(BaseModel):
    """One sub-folder of a folder: where it is and what it holds."""

    model_config = _CONFIG

    path: str = Field(description="The sub-folder's own path; read it with /v1/honey/tree.")
    name: str = Field(description="Its last path segment: hive, a Cell's id, wax.")
    title: str = Field(description="What it holds, in a few words.")
    detail: str = Field(description="A short qualifier, e.g. how many rows it holds; may be empty.")

    @classmethod
    def of_entry(cls, entry: BrowseEntry) -> HoneyFolderEntryView:
        """Shape one FOLDER entry of a listing.

        Args:
            entry: A folder entry.

        Returns:
            The sub-folder as the view shows it.
        """
        return cls(
            path=entry.path, name=last_segment(entry.path), title=entry.title, detail=entry.detail
        )


class HoneyFolderView(BaseModel):
    """One folder of the Honey tree: its sub-folders and a page of its items."""

    model_config = _CONFIG

    path: str = Field(description="The folder, normalised.")
    parent: str | None = Field(description="Its parent folder; null for the root.")
    scope: str | None = Field(
        description="The scope the folder shows (hive, cell:<id> ...); null for the root, an "
        "index folder and /bee-bread, which gather what the viewer may see."
    )
    can_search: bool = Field(description="Whether /v1/honey/search can search this folder.")
    ceiling: HoneyClearance = Field(
        description="The most sensitive label this viewer may read; items above it are not listed."
    )
    folders: list[HoneyFolderEntryView] = Field(
        description="Sub-folders the viewer's observe:honey capabilities reach."
    )
    items: list[HoneyItemSummary] = Field(description="One page of the folder's visible items.")
    next_cursor: str | None = Field(description="Pass as ?cursor= for the next page; null at end.")
    note: str = Field(description="Why the listing is what it is, when not obvious; else empty.")

    @classmethod
    def of_listing(
        cls, listing: BrowseListing, ceiling: HoneyClearance, offset: int, limit: int
    ) -> HoneyFolderView:
        """Shape one page of a folder for a viewer the browser already filtered it for.

        Args:
            listing: The browser's page of the folder, filtered by the viewer's reader.
            ceiling: The most sensitive label the viewer may read.
            offset: Where the page started.
            limit: How many entries the page asked for.

        Returns:
            The folder: sub-folders and items apart, the cursor for the next page.
        """
        segments = [segment for segment in listing.path.split(PATH_SEPARATOR) if segment]
        folders = [e for e in listing.entries if e.kind is EntryKind.FOLDER]
        items = [e for e in listing.entries if e.kind is not EntryKind.FOLDER]
        target = parse_path(listing.path)
        return cls(
            path=listing.path,
            parent=PATH_SEPARATOR + PATH_SEPARATOR.join(segments[:-1]) if segments else None,
            scope=target.scope,
            can_search=target.kind in _SEARCHABLE,
            ceiling=ceiling,
            folders=[HoneyFolderEntryView.of_entry(entry) for entry in folders],
            items=[HoneyItemSummary.of_entry(entry) for entry in items],
            next_cursor=encode_honey_cursor(offset + limit) if listing.is_truncated else None,
            note=listing.note,
        )
