"""Define what one Honey browser listing shows: its entries, and how each kind of item becomes one.

The Honey browser presents the Honey Store (the Hive's cold-tier knowledge base) as a read-only
folder tree; `ls` on a folder returns a `BrowseListing`, a page of `BrowseEntry` rows. Every entry
names its own path and kind and carries a one-line title and detail; every item that is not a
folder (a Honey row, a live Cell Wax note, a Bee Bread entry) also shows its `HoneyClearance` (the
data-sensitivity label C0/C1/C2) and its provenance (task, Cell, bee, when), so a reader can weigh
it before opening it. The builders here are the one place each kind of item becomes an entry.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package's
    browse sub-package. Built by `hivemind.honey_store.browse.folders` and `.browser`; rendered by
    `hive honey ls` and, later, the Observation Hive. Calls into `hivemind.honey_store.models`,
    this package's `paths` and `sources`, `hivemind.cell` and `waggle` only.

Key invariants:
    - A folder entry carries no clearance and no provenance; every other entry carries both.
    - Nothing here decides what a reader may see: the browser filters first, then builds entries.

See Also:
    - hivemind.honey_store.browse.folders for the listings built from these entries.
    - waggle.messages.honey.hit.HoneyProvenance, the provenance shape reused here.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.paths import bee_bread_entry_path, wax_note_path
from hivemind.honey_store.browse.sources import BeeBreadNote, WaxNote
from hivemind.honey_store.models import Honey, HoneyPart
from hivemind.honey_store.scope import cell_scope
from waggle.messages.honey import HoneyProvenance

MAX_ENTRY_TITLE_CHARS = 120  # One line in a listing; the document itself carries the rest.

# codingrules 8.5: frozen, extra-forbidding config both listing models share.
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
_ELLIPSIS = "…"  # Marks a title cut to fit one line.

__all__ = [
    "MAX_ENTRY_TITLE_CHARS",
    "BrowseEntry",
    "BrowseListing",
    "EntryKind",
    "bee_bread_entry",
    "folder_entry",
    "honey_entry",
    "wax_entry",
]


class EntryKind(Enum):
    """What one listed entry is: a folder to `ls`, or a document to `cat`."""

    FOLDER = "FOLDER"  # A folder: the root's five, a scope, a Cell's wax folder.
    HONEY = "HONEY"  # One ripened Honey row.
    WAX = "WAX"  # One live Cell Wax note.
    BEE_BREAD = "BEE_BREAD"  # One Bee Bread entry.


class BrowseEntry(BaseModel):
    """One row of a listing: where it is, what it is, and (for a document) its label and source."""

    model_config = _MODEL_CONFIG

    path: str = Field(description="The entry's own browser path; `ls` or `cat` it.")
    kind: EntryKind = Field(description="A folder, a Honey row, a wax note or a Bee Bread entry.")
    title: str = Field(max_length=MAX_ENTRY_TITLE_CHARS, description="A one-line label.")
    detail: str = Field(
        description="A short qualifier: a Honey row's part, a note's severity, an entry's kind, "
        "or how many rows a scope folder holds."
    )
    clearance: HoneyClearance | None = Field(
        description="The item's data-sensitivity label; None for a folder."
    )
    provenance: HoneyProvenance | None = Field(
        description="The task, Cell, bee and time the item came from; None for a folder."
    )


class BrowseListing(BaseModel):
    """One page of one folder's contents, as `ls` returns it."""

    model_config = _MODEL_CONFIG

    path: str = Field(description="The folder listed, normalised.")
    entries: tuple[BrowseEntry, ...] = Field(description="The page's entries, in listing order.")
    is_truncated: bool = Field(
        description="True when more entries exist than this page shows (a later page, or a "
        "scan that stopped at its bound)."
    )
    note: str = Field(default="", description="Why the listing is what it is, when not obvious.")


def folder_entry(path: str, title: str, detail: str = "") -> BrowseEntry:
    """Build the entry for one folder.

    Args:
        path: The folder's own path.
        title: What the folder holds, in a few words.
        detail: A short qualifier, e.g. how many rows it holds; empty when none.

    Returns:
        A FOLDER entry with no clearance and no provenance.
    """
    return BrowseEntry(
        path=path,
        kind=EntryKind.FOLDER,
        title=_one_line(title),
        detail=detail,
        clearance=None,
        provenance=None,
    )


def honey_entry(honey: Honey) -> BrowseEntry:
    """Build the entry for one Honey row.

    Args:
        honey: The row, already known visible to the reader.

    Returns:
        A HONEY entry: its path, title, part (and chunk index), clearance and provenance.
    """
    # A SUMMARY row stands for the whole Nectar; a CHUNK row names which piece of its text it is.
    part = "SUMMARY" if honey.part is HoneyPart.SUMMARY else f"CHUNK {honey.chunk_index}"
    return BrowseEntry(
        path=honey.path,
        kind=EntryKind.HONEY,
        title=_one_line(honey.title),
        detail=part,
        clearance=honey.clearance,
        provenance=HoneyProvenance(
            task_id=honey.task_id,
            cell_id=honey.cell_id,
            bee=honey.bee,
            observed_at=honey.observed_at,
        ),
    )


def wax_entry(note: WaxNote) -> BrowseEntry:
    """Build the entry for one live Cell Wax note.

    Args:
        note: The note, already known visible and live.

    Returns:
        A WAX entry: its path, text as title, severity, clearance and provenance.
    """
    return BrowseEntry(
        path=wax_note_path(cell_scope(note.cell_id), note.id),
        kind=EntryKind.WAX,
        title=_one_line(note.text),
        detail=note.severity.value,
        clearance=note.clearance,
        provenance=HoneyProvenance(
            task_id=note.task_id,
            cell_id=note.cell_id,
            bee=note.proposer,
            observed_at=note.proposed_at,
        ),
    )


def bee_bread_entry(note: BeeBreadNote) -> BrowseEntry:
    """Build the entry for one Bee Bread entry.

    Args:
        note: The entry, already known visible.

    Returns:
        A BEE_BREAD entry: its path, preview (or kind) as title, kind, clearance and provenance.
    """
    # An index-only entry carries a preview; a payload entry's first line stands in for one.
    preview = note.text if note.text else (note.payload or note.kind)
    return BrowseEntry(
        path=bee_bread_entry_path(note.id),
        kind=EntryKind.BEE_BREAD,
        title=_one_line(preview),
        detail=note.kind,
        clearance=note.clearance,
        provenance=HoneyProvenance(
            task_id=note.task_id, cell_id=None, bee=None, observed_at=note.created_at
        ),
    )


def _one_line(text: str) -> str:
    """Return `text`'s first line, cut to MAX_ENTRY_TITLE_CHARS with an ellipsis when longer."""
    # A listing shows one line per entry; the document itself carries the rest.
    first = text.strip().splitlines()[0] if text.strip() else ""
    if len(first) <= MAX_ENTRY_TITLE_CHARS:
        return first
    # Cut, and say so with an ellipsis, so a cut title is never mistaken for the whole line.
    return first[: MAX_ENTRY_TITLE_CHARS - len(_ELLIPSIS)] + _ELLIPSIS
