"""Print what `hive honey` returns: listings, documents, query hits, store counts, slot status.

Output formatting only (codingrules section 2's CLI row): every value printed here was produced
by `hivemind.honey_store` -- a `BrowseListing`, a document, a `HoneyResponse`, a `HoneyStats` --
and this module decides only how it looks on a terminal, or as JSON for `--json`. Retrieved text
is shown as reference data, never run: hits and documents are printed, nothing more (codingrules
section 15, "Nothing is executed from the Honey Store").

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `honey` command group. Called by
    `hivemind.cli.honey.query`, `.browse` and `.maintain`. Calls into `hivemind.honey_store` (the
    shapes it prints) and `hivemind.cli.honey.context` (SlotStatus) only.

Key invariants:
    - Nothing here reads a store or decides what a reader may see; it prints what it is given.
    - Every `--json` document is one JSON value on stdout.

See Also:
    - hivemind.honey_store.browse for BrowseListing and the document shapes.
    - waggle.messages.honey for HoneyResponse and HoneyHit.
"""

from __future__ import annotations

import json

import typer

from hivemind.cli.honey.context import SlotStatus
from hivemind.honey_store import Honey, HoneyStats
from hivemind.honey_store.browse import (
    BeeBreadNote,
    BrowseDocument,
    BrowseListing,
    EntryKind,
    WaxNote,
)
from waggle.messages.honey import HoneyProvenance, HoneyResponse

_NONE = "-"  # Printed for a field that has no value.
_INDENT = "    "  # A continuation line under one listed entry or hit.

__all__ = [
    "document_json",
    "print_document",
    "print_listing",
    "print_response",
    "print_slot",
    "print_stats",
]


def print_listing(listing: BrowseListing) -> None:
    """Print one folder page: a line per entry, its provenance under it when it has one.

    Args:
        listing: The page `HoneyBrowser.ls` returned.
    """
    typer.echo(f"{listing.path}  ({len(listing.entries)} entries)")
    # One line per entry; a document's provenance follows on an indented line of its own.
    for entry in listing.entries:
        label = entry.clearance.value if entry.clearance is not None else _NONE
        typer.echo(
            f"{label:<3}  {entry.kind.value:<9}  {entry.detail:<10}  {entry.path}  {entry.title}"
        )
        if entry.provenance is not None:
            typer.echo(f"{_INDENT}{_provenance(entry.provenance)}")
    # Say when the page is not the whole folder, and why when the browser gave a reason.
    if listing.is_truncated:
        typer.echo("more entries exist: pass --offset to see the next page")
    if listing.note:
        typer.echo(f"note: {listing.note}")


def print_document(path: str, document: BrowseDocument) -> None:
    """Print one document: its labelled fields, then its text.

    Args:
        path: The document's own path.
        document: The Honey row, wax note or Bee Bread entry `HoneyBrowser.cat` returned.
    """
    typer.echo(f"path: {path}")
    for name, value in _fields(document):
        typer.echo(f"{name}: {value}")
    for heading, text in _texts(document):
        typer.echo(f"\n{heading}:\n{text}")


def document_json(path: str, document: BrowseDocument) -> str:
    """Return one document as JSON: its path, its kind and every field it carries.

    Args:
        path: The document's own path.
        document: The Honey row, wax note or Bee Bread entry.

    Returns:
        One JSON object with `path`, `kind` (HONEY, WAX or BEE_BREAD) and `document`.
    """
    payload = {
        "path": path,
        "kind": _document_kind(document).value,
        "document": document.model_dump(mode="json"),
    }
    return json.dumps(payload, indent=2)


def print_response(response: HoneyResponse) -> None:
    """Print a query's hits, best first, each with its label, score, path and excerpt.

    Args:
        response: What the retriever answered.
    """
    # How the search ran and what it withheld comes first, before any hit.
    typer.echo(response.reason)
    # Best first; each hit's provenance and excerpt follow, indented, as reference data only.
    for rank, hit in enumerate(response.hits, start=1):
        typer.echo(
            f"{rank:>2}. [{hit.clearance.value}] {hit.score:.3f}  {hit.honey_ref}  {hit.title}"
        )
        typer.echo(f"{_INDENT}{_provenance(hit.provenance)}")
        for line in hit.excerpt.splitlines():
            typer.echo(f"{_INDENT}{line}")
    typer.echo(
        f"hits={len(response.hits)}  tokens={response.token_count}  "
        f"withheld={response.filtered_count}  truncated={response.is_truncated}"
    )


def print_stats(stats: HoneyStats) -> None:
    """Print the store's counts: Nectar by state, live Honey by part, label and scope, vectors.

    Args:
        stats: `HoneyStore.stats()`'s counts.
    """
    typer.echo(
        f"nectar:     {_counts({state.value: n for state, n in stats.nectar_by_state.items()})}"
    )
    typer.echo(f"honey:      {_counts({part.value: n for part, n in stats.honey_by_part.items()})}")
    typer.echo(f"            tainted={stats.honey_tainted}  retired={stats.honey_retired}")
    typer.echo(
        f"clearance:  {_counts({label.value: n for label, n in stats.honey_by_clearance.items()})}"
    )
    typer.echo(f"scopes:     {_counts(stats.honey_by_scope_kind)}")
    # ADR-0032: coverage per embedding model, so a re-embed's progress is visible.
    typer.echo(f"vectors:    {_counts(stats.vectors_by_model)}")
    typer.echo(f"vector search: {stats.vector_backend}")


def print_slot(name: str, status: SlotStatus, without: str) -> None:
    """Print one model slot's status: the model it serves, or why none and what that costs.

    Args:
        name: The slot's own name ("embedder", "ripener").
        status: How the slot resolved.
        without: What the Honey Store does with no model in this slot.
    """
    # A usable slot: its model is all there is to say.
    if status.model is not None:
        typer.echo(f"{name}: {status.model}")
        return
    typer.echo(f"{name}: none ({status.reason}); {without}")


def _counts(counts: dict[str, int]) -> str:
    """Render `{name: count}` as `name=count` pairs, sorted by name; `-` when empty."""
    # Nothing of this kind at all: a visible dash, never a blank line.
    if not counts:
        return _NONE
    return "  ".join(f"{name}={count}" for name, count in sorted(counts.items()))


def _provenance(provenance: HoneyProvenance) -> str:
    """Render where an item came from: task, Cell, bee and when."""
    return (
        f"task={provenance.task_id or _NONE}  cell={provenance.cell_id or _NONE}  "
        f"bee={provenance.bee or _NONE}  observed={provenance.observed_at.isoformat()}"
    )


def _fields(document: BrowseDocument) -> tuple[tuple[str, str], ...]:
    """Return a document's labelled metadata fields, in display order."""
    # A Honey row: its label and filing, where it came from, and which models wrote it.
    if isinstance(document, Honey):
        return (
            ("title", document.title),
            ("clearance", document.clearance.value),
            ("scope", document.scope),
            ("kind", document.kind.value),
            ("origin", document.origin.value),
            ("part", f"{document.part.value} {document.chunk_index}"),
            ("provenance", _provenance(_honey_provenance(document))),
            ("embedding model", document.embedding_model or _NONE),
            ("ripener model", document.ripener_model or _NONE),
            ("nectar", document.nectar_id),
        )
    # A wax note: how much it weighs, its label, who noticed it and whether it expires.
    if isinstance(document, WaxNote):
        return (
            ("severity", document.severity.value),
            ("clearance", document.clearance.value),
            ("origin", document.origin.value),
            ("proposer", document.proposer or _NONE),
            ("task", document.task_id or _NONE),
            ("proposed", document.proposed_at.isoformat()),
            ("expires", document.expires_at.isoformat() if document.expires_at else _NONE),
        )
    return _bee_bread_fields(document)


def _bee_bread_fields(document: BeeBreadNote) -> tuple[tuple[str, str], ...]:
    """Return a Bee Bread entry's labelled metadata fields, in display order."""
    return (
        ("kind", document.kind),
        ("clearance", document.clearance.value),
        ("task", document.task_id or _NONE),
        ("created", document.created_at.isoformat()),
        ("refs", ", ".join(document.ref_ids) or _NONE),
    )


def _texts(document: BrowseDocument) -> tuple[tuple[str, str], ...]:
    """Return a document's text blocks, headed, in display order; empty blocks left out."""
    # Each kind's own text: a row's summary and body, a note's text and reason, an entry's
    # preview and payload.
    if isinstance(document, Honey):
        blocks = (("summary", document.summary), ("body", document.body))
    elif isinstance(document, WaxNote):
        blocks = (("text", document.text), ("reason", document.reason))
    else:
        blocks = (("preview", document.text or ""), ("payload", document.payload or ""))
    return tuple((heading, text) for heading, text in blocks if text)


def _document_kind(document: BrowseDocument) -> EntryKind:
    """Name a document's kind the way a listing entry names it."""
    if isinstance(document, Honey):
        return EntryKind.HONEY
    return EntryKind.WAX if isinstance(document, WaxNote) else EntryKind.BEE_BREAD


def _honey_provenance(honey: Honey) -> HoneyProvenance:
    """Return a Honey row's provenance in the shape every hit carries."""
    return HoneyProvenance(
        task_id=honey.task_id, cell_id=honey.cell_id, bee=honey.bee, observed_at=honey.observed_at
    )
