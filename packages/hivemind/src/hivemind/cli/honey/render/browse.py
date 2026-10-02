"""Print what `hive honey ls` and `cat` return: a folder page, one document, a document's JSON.

Output formatting only (codingrules section 2's CLI row): a `BrowseListing` and the three document
shapes (a Honey row, a wax note, a Bee Bread entry) come from `hivemind.honey_store.browse`, and
this module decides only how they look on a terminal, or as JSON for `--json`. A document's text
is shown as reference data, never run (codingrules section 15, "Nothing is executed from the Honey
Store").

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.honey.render`. Re-exported
    by the package's face. Calls into `hivemind.honey_store` (the shapes it prints) and this
    package's `common` only.

Key invariants:
    - Nothing here reads a store or decides what a reader may see; it prints what it is given.
    - A Honey row's extra sources (ADR-0037) are printed only when it has any, and never carry
      content: task, Cell, bee, when, origin, tier and the label each declared.

See Also:
    - hivemind.honey_store.browse for BrowseListing and the document shapes.
"""

from __future__ import annotations

import json

import typer

from hivemind.cli.honey.render.common import INDENT, NONE, provenance
from hivemind.honey_store import Honey, NectarSource
from hivemind.honey_store.browse import (
    BeeBreadNote,
    BrowseDocument,
    BrowseListing,
    EntryKind,
    HoneyDocument,
    WaxNote,
)
from waggle.messages.honey import HoneyProvenance

__all__ = ["document_json", "print_document", "print_listing"]


def print_listing(listing: BrowseListing) -> None:
    """Print one folder page: a line per entry, its provenance under it when it has one.

    Args:
        listing: The page `HoneyBrowser.ls` returned.
    """
    typer.echo(f"{listing.path}  ({len(listing.entries)} entries)")
    # One line per entry; a document's provenance follows on an indented line of its own.
    for entry in listing.entries:
        label = entry.clearance.value if entry.clearance is not None else NONE
        typer.echo(
            f"{label:<3}  {entry.kind.value:<9}  {entry.detail:<10}  {entry.path}  {entry.title}"
        )
        if entry.provenance is not None:
            typer.echo(f"{INDENT}{provenance(entry.provenance)}")
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
    # ADR-0037: whoever else deposited the same text, under the row's own provenance; only a
    # Honey row read through `cat` carries them, and only when someone did.
    if isinstance(document, HoneyDocument) and document.sources:
        typer.echo(f"also deposited by ({len(document.sources)}):")
        for source in document.sources:
            typer.echo(f"{INDENT}{_source(source)}")
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
            ("provenance", provenance(_honey_provenance(document))),
            ("embedding model", document.embedding_model or NONE),
            ("ripener model", document.ripener_model or NONE),
            ("nectar", document.nectar_id),
        )
    # A wax note: how much it weighs, its label, who noticed it and whether it expires.
    if isinstance(document, WaxNote):
        return (
            ("severity", document.severity.value),
            ("clearance", document.clearance.value),
            ("origin", document.origin.value),
            ("proposer", document.proposer or NONE),
            ("task", document.task_id or NONE),
            ("proposed", document.proposed_at.isoformat()),
            ("expires", document.expires_at.isoformat() if document.expires_at else NONE),
        )
    return _bee_bread_fields(document)


def _bee_bread_fields(document: BeeBreadNote) -> tuple[tuple[str, str], ...]:
    """Return a Bee Bread entry's labelled metadata fields, in display order."""
    return (
        ("kind", document.kind),
        ("clearance", document.clearance.value),
        ("task", document.task_id or NONE),
        ("created", document.created_at.isoformat()),
        ("refs", ", ".join(document.ref_ids) or NONE),
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


def _source(source: NectarSource) -> str:
    """Render one extra depositor: where and when it came from and what it declared; no content."""
    return (
        f"task={source.task_id or NONE}  cell={source.cell_id}  bee={source.bee or NONE}  "
        f"observed={source.observed_at.isoformat()}  origin={source.origin.value}  "
        f"tier={source.origin_tier.value}  declared={source.clearance.value}  "
        f"key={source.source_key or NONE}"
    )
