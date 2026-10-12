"""Define the Honey browser's item models: what one item is, where it came from, and its text.

The Observation Hive's Honey browser (roadmap step 12.9) shows the Hive's knowledge as a folder
tree over provenance (codingrules 8.11). An item is one thing in a folder: a ripened Honey row, a
note the human proposed, one live Cell Wax note, or one Bee Bread entry. ``HoneyItemSummary`` is
what a folder listing shows of it: its kind, label, provenance (task, Cell, bee, the source it
came by and when), its ``HoneyClearance``, its size and, for wax, its severity, proposer and
expiry. ``HoneyItemView`` adds the item's text. Each is built from what ``hivemind.honey_store.
browse`` already filtered for the viewer (``of_entry``, ``of_document``), so a view can never
show more than the browser let through.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.views.honey``.
    Built by ``hivemind.entrance.reads.honey`` and carried by the folder and search models beside
    it; published in the OpenAPI document. Calls into the Honey browser's listing and document
    types and pydantic.

Key invariants:
    - Pure: a record is handed in and a view comes out; nothing is read or widened.
    - An item's text is in ``HoneyItemView`` alone; a listing carries a one-line title.
    - A listed item always has provenance and a label; only a folder lacks them.

See Also:
    - hivemind.honey_store.browse.listing for the entries these reshape.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.common.errors import InvariantViolationError
from hivemind.honey_store.browse import (
    BrowseDocument,
    BrowseEntry,
    EntryKind,
    HoneyDocument,
    WaxNote,
    bee_bread_entry_path,
    bee_bread_scope,
    wax_note_path,
)
from hivemind.honey_store.models import Honey, HoneyPart
from hivemind.honey_store.scope import cell_scope
from waggle.messages.base import CellIdField, TaskIdField, UtcDatetime
from waggle.messages.cell.wax import WaxSeverity
from waggle.messages.honey import HoneyProvenance

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

MAX_TITLE_CHARS = 120  # A title is one line, as the browser's own listing cuts it.
HUMAN_SOURCE = "HUMAN"  # The Nectar origin of a note the human proposed.
BEE_BREAD_SOURCE = "BEE_BREAD"  # What a Bee Bread entry's source says.
PATH_SEPARATOR = "/"  # Between a path's segments.

__all__ = [
    "PATH_SEPARATOR",
    "HoneyItemKind",
    "HoneyItemSummary",
    "HoneyItemView",
    "HoneyProvenanceView",
    "last_segment",
    "provenance_view",
]


class HoneyItemKind(Enum):
    """What one item in the Honey browser is; the vocabulary may grow (x-hive-open)."""

    HONEY = "honey"  # A ripened Honey row: knowledge with a label and a source.
    NOTE = "note"  # A Honey row the human proposed as a note (its Nectar came from the human).
    WAX = "wax"  # One live Cell Wax note, shown in its Cell's folder.
    BEE_BREAD = "bee_bread"  # One Bee Bread entry (the warm memory tier), not yet ripened.


class HoneyProvenanceView(BaseModel):
    """Where an item came from: its task, Cell, bee, the source it came by and when."""

    model_config = _CONFIG

    task_id: TaskIdField | None = Field(description="The task it came from; null when none.")
    cell_id: CellIdField | None = Field(description="The Cell it was gathered on; null if unknown.")
    bee: str | None = Field(description="The Worker or Warden that gathered it; null if none.")
    source: str | None = Field(
        description="How it came to be: a Nectar origin (BEE, HUMAN, WATCH, TASK_OUTCOME ...), a "
        "wax note's origin (BEE, PATROL, HUMAN) or BEE_BREAD; null for a search hit."
    )
    observed_at: UtcDatetime = Field(description="When it was observed (a note: proposed).")


class HoneyItemSummary(BaseModel):
    """One item in a folder: enough to weigh it before opening it (observe:honey:<scope>)."""

    model_config = _CONFIG

    id: str = Field(description="The item's own id; open it with /v1/honey/items/{id}.")
    path: str = Field(description="The item's own browser path, its place in the tree.")
    kind: HoneyItemKind = Field(description="A Honey row, a human's note, a wax note or an entry.")
    title: str = Field(description="A one-line label.")
    detail: str = Field(
        description="A short qualifier: a Honey row's part, a wax note's severity, an entry's kind."
    )
    scope: str | None = Field(description="The scope it is filed under; decides who may read it.")
    clearance: HoneyClearance = Field(description="The item's data-sensitivity label.")
    provenance: HoneyProvenanceView = Field(description="Task, Cell, bee, source and when.")
    size_chars: int = Field(description="The item's text length in characters.")
    severity: WaxSeverity | None = Field(description="A wax note's weight; null for other items.")
    proposer: str | None = Field(description="A wax note's proposing bee; null for the human's.")
    expires_at: UtcDatetime | None = Field(
        description="When a wax note lapses on its own; null for standing wax and other items."
    )

    @classmethod
    def of_entry(cls, entry: BrowseEntry) -> HoneyItemSummary:
        """Shape one listed item (never a folder).

        Args:
            entry: A HONEY, WAX or BEE_BREAD entry of a listing, already visible to the viewer.

        Returns:
            Its summary: id, kind, label, provenance and, for wax, severity, proposer and expiry.
        """
        provenance = entry.provenance
        is_wax = entry.kind is EntryKind.WAX
        return cls(
            id=last_segment(entry.path),
            path=entry.path,
            kind=_kind_of(entry.kind, entry.source),
            title=entry.title,
            detail=entry.detail,
            scope=entry.scope,
            # Only a folder lacks a label, and folders never reach this builder.
            clearance=entry.clearance or HoneyClearance.C2,
            provenance=provenance_view(provenance, entry.source),
            size_chars=entry.size_chars,
            severity=WaxSeverity(entry.detail) if is_wax else None,
            proposer=provenance.bee if provenance is not None and is_wax else None,
            expires_at=entry.expires_at,
        )


class HoneyItemView(HoneyItemSummary):
    """One item with its text (observe:honey:<scope>, and clearance within the viewer's)."""

    summary: str | None = Field(description="A Honey row's gist; null for other items.")
    content: str = Field(description="The item's text: a row's body, a note's words, an entry.")
    reason: str | None = Field(description="Why a wax note's proposer believed it; else null.")
    sources: list[HoneyProvenanceView] = Field(
        description="A Honey row's other distinctly-provenanced deposits of the same content "
        "(who else deposited it, never what); empty for other items."
    )

    @classmethod
    def of_document(cls, document: BrowseDocument) -> HoneyItemView:
        """Shape one document the browser read for the viewer, with its text.

        Args:
            document: A Honey row (with its extra sources), a live wax note or a Bee Bread entry.

        Returns:
            The item and its text.
        """
        if isinstance(document, Honey):
            return _honey_view(document)
        if isinstance(document, WaxNote):
            return _wax_view(document)
        text = document.payload or document.text or ""
        return HoneyItemView(
            id=document.id,
            path=bee_bread_entry_path(document.id),
            kind=HoneyItemKind.BEE_BREAD,
            title=_first_line(text) or document.kind,
            detail=document.kind,
            scope=bee_bread_scope(document),
            clearance=document.clearance,
            provenance=HoneyProvenanceView(
                task_id=document.task_id,
                cell_id=None,
                bee=None,
                source=BEE_BREAD_SOURCE,
                observed_at=document.created_at,
            ),
            size_chars=len(text),
            severity=None,
            proposer=None,
            expires_at=None,
            summary=None,
            content=text,
            reason=None,
            sources=[],
        )


def last_segment(path: str) -> str:
    """Return a path's last segment: an item's id, a folder's name."""
    return path.rsplit(PATH_SEPARATOR, 1)[-1]


def provenance_view(provenance: HoneyProvenance | None, source: str | None) -> HoneyProvenanceView:
    """Reshape the browser's provenance, adding the source the item came by.

    Args:
        provenance: What the browser or a search hit carries; None only for a folder.
        source: How the item came to be, if known; empty or None leaves it null.

    Returns:
        The provenance as the view shows it.

    Raises:
        InvariantViolationError: A listed item carried no provenance.
    """
    # A listed item always has provenance; the type allows None only for a folder.
    if provenance is None:
        raise InvariantViolationError("A listed Honey item carries provenance.")
    return HoneyProvenanceView(
        task_id=provenance.task_id,
        cell_id=provenance.cell_id,
        bee=provenance.bee,
        source=source or None,
        observed_at=provenance.observed_at,
    )


def _honey_view(honey: Honey) -> HoneyItemView:
    """Shape a Honey row, with the other deposits its Nectar absorbed when `cat` found them."""
    extra = honey.sources if isinstance(honey, HoneyDocument) else ()
    part = "SUMMARY" if honey.part is HoneyPart.SUMMARY else f"CHUNK {honey.chunk_index}"
    return HoneyItemView(
        id=honey.id,
        path=honey.path,
        kind=_kind_of(EntryKind.HONEY, honey.origin.value),
        title=honey.title,
        detail=part,
        scope=honey.scope,
        clearance=honey.clearance,
        provenance=HoneyProvenanceView(
            task_id=honey.task_id,
            cell_id=honey.cell_id,
            bee=honey.bee,
            source=honey.origin.value,
            observed_at=honey.observed_at,
        ),
        size_chars=len(honey.body),
        severity=None,
        proposer=None,
        expires_at=None,
        summary=honey.summary,
        content=honey.body,
        reason=None,
        sources=[
            HoneyProvenanceView(
                task_id=source.task_id,
                cell_id=source.cell_id,
                bee=source.bee,
                source=source.origin.value,
                observed_at=source.observed_at,
            )
            for source in extra
        ],
    )


def _wax_view(note: WaxNote) -> HoneyItemView:
    """Shape a live Cell Wax note: its words, why it was believed, its weight and its expiry."""
    scope = cell_scope(note.cell_id)
    return HoneyItemView(
        id=note.id,
        path=wax_note_path(scope, note.id),
        kind=HoneyItemKind.WAX,
        title=_first_line(note.text),
        detail=note.severity.value,
        scope=scope,
        clearance=note.clearance,
        provenance=HoneyProvenanceView(
            task_id=note.task_id,
            cell_id=note.cell_id,
            bee=note.proposer,
            source=note.origin.value,
            observed_at=note.proposed_at,
        ),
        size_chars=len(note.text),
        severity=note.severity,
        proposer=note.proposer,
        expires_at=note.expires_at,
        summary=None,
        content=note.text,
        reason=note.reason,
        sources=[],
    )


def _kind_of(kind: EntryKind, source: str) -> HoneyItemKind:
    """Name an item's kind: a wax note, an entry, the human's own note or other Honey."""
    if kind is EntryKind.WAX:
        return HoneyItemKind.WAX
    if kind is EntryKind.BEE_BREAD:
        return HoneyItemKind.BEE_BREAD
    # A row ripened from the human's own proposed note is a note; every other row is Honey.
    return HoneyItemKind.NOTE if source == HUMAN_SOURCE else HoneyItemKind.HONEY


def _first_line(text: str) -> str:
    """Return the first line of `text`, trimmed: a wax note's or an entry's title."""
    stripped = text.strip()
    return stripped.splitlines()[0][:MAX_TITLE_CHARS] if stripped else ""
