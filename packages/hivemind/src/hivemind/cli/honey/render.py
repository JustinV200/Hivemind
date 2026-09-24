"""Print what `hive honey` returns: listings, documents, hits, counts, proposals, slot status.

Output formatting only (codingrules section 2's CLI row): every value printed here was produced
by `hivemind.honey_store` -- a `BrowseListing`, a document, a `HoneyResponse`, a `HoneyStats`, a
label lowering proposal (ADR-0034) and what the judge's review did, a prune's outcome -- and this
module decides only how it looks on a terminal, or as JSON for `--json`. `ReviewEntry` is the one
shape defined here: a proposal beside the title and browser path `hive honey review` looked up
for it. Retrieved text is shown as reference data, never run: hits and documents are printed,
nothing more (codingrules section 15, "Nothing is executed from the Honey Store").

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `honey` command group. Called by
    `hivemind.cli.honey.query`, `.browse`, `.maintain` and `.review`. Calls into
    `hivemind.honey_store` (the shapes it prints) and `hivemind.cli.honey.context` (SlotStatus)
    only.

Key invariants:
    - Nothing here reads a store or decides what a reader may see; it prints what it is given (a
      withheld title arrives as None and is printed as withheld).
    - Every `--json` document is one JSON value on stdout.
    - A Honey row's extra sources (ADR-0033) are printed only when it has any, and never carry
      content: task, Cell, bee, when, origin, tier and the label each declared.

See Also:
    - hivemind.honey_store.browse for BrowseListing and the document shapes.
    - hivemind.honey_store.lowering for LoweringProposal and ReviewOutcome.
    - waggle.messages.honey for HoneyResponse and HoneyHit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import typer

from hivemind.cli.honey.context import SlotStatus
from hivemind.honey_store import (
    Honey,
    HoneyStats,
    LoweringProposal,
    LoweringState,
    NectarSource,
    PruneOutcome,
    ReviewOutcome,
)
from hivemind.honey_store.browse import (
    BeeBreadNote,
    BrowseDocument,
    BrowseListing,
    EntryKind,
    HoneyDocument,
    WaxNote,
)
from waggle.messages.honey import HoneyProvenance, HoneyResponse

_NONE = "-"  # Printed for a field that has no value.
_INDENT = "    "  # A continuation line under one listed entry or hit.
_WITHHELD = "(title above --clearance)"  # A proposal's title the reader's ceiling hides.
_DECIDE_HINT = "decide one with: hive honey review approve|deny <id> --reason TEXT"

__all__ = [
    "ReviewEntry",
    "document_json",
    "print_decision",
    "print_document",
    "print_listing",
    "print_prune",
    "print_response",
    "print_review",
    "print_review_entry",
    "print_slot",
    "print_stats",
    "review_counts",
    "review_json",
]


@dataclass(frozen=True, slots=True)
class ReviewEntry:
    """One label lowering proposal as `hive honey review` shows it, with what it would lower."""

    proposal: LoweringProposal  # Exactly as the store returned it.
    title: str | None  # Its Nectar's title; None when that label is above the reader's ceiling.
    path: (
        str | None
    )  # Its SUMMARY row's browser path, for `hive honey cat`; None when none is live.


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
    # ADR-0033: whoever else deposited the same text, under the row's own provenance; only a
    # Honey row read through `cat` carries them, and only when someone did.
    if isinstance(document, HoneyDocument) and document.sources:
        typer.echo(f"also deposited by ({len(document.sources)}):")
        for source in document.sources:
            typer.echo(f"{_INDENT}{_source(source)}")
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


def print_review(entries: tuple[ReviewEntry, ...], is_truncated: bool) -> None:
    """Print a review listing: one block per proposal, then who each waiting one waits for.

    Args:
        entries: The proposals to show, in listing order (waiting ones first).
        is_truncated: Whether more proposals exist past the listing's limit.
    """
    # Nothing filed yet: say so, rather than print an empty table.
    if not entries:
        typer.echo("no label lowering proposals")
        return
    for entry in entries:
        print_review_entry(entry)
    waiting = [e.proposal for e in entries if e.proposal.state is LoweringState.PROPOSED]
    for_human = sum(1 for proposal in waiting if proposal.note)
    typer.echo(
        f"{len(entries)} proposal(s): {len(waiting) - for_human} waiting for the judge, "
        f"{for_human} waiting for the human; {_DECIDE_HINT}"
    )
    if is_truncated:
        typer.echo("more proposals exist: pass a larger --limit")


def print_review_entry(entry: ReviewEntry) -> None:
    """Print one proposal: its state, id, labels and what it concerns, then its reasons.

    Args:
        entry: The proposal, with its Nectar's title (None when withheld) and browser path.
    """
    proposal = entry.proposal
    title = _WITHHELD if entry.title is None else (entry.title or _NONE)
    typer.echo(
        f"{proposal.state.value:<8}  {proposal.id}  {proposal.from_label.value} -> "
        f"{proposal.to_label.value}  attempts={proposal.attempts}  {entry.path or _NONE}  {title}"
    )
    for name, value in _proposal_details(proposal):
        typer.echo(f"{_INDENT}{name}: {value}")


def review_json(entries: tuple[ReviewEntry, ...], is_truncated: bool) -> str:
    """Return a review listing as one JSON object: every proposal's fields, title and path.

    Args:
        entries: The proposals to show, in listing order.
        is_truncated: Whether more proposals exist past the listing's limit.

    Returns:
        `{"proposals": [...], "is_truncated": bool}`; a withheld title is null.
    """
    proposals = [
        {**entry.proposal.model_dump(mode="json"), "title": entry.title, "path": entry.path}
        for entry in entries
    ]
    return json.dumps({"proposals": proposals, "is_truncated": is_truncated}, indent=2)


def review_counts(outcome: ReviewOutcome) -> str:
    """Render what one judge review step did, as `name=count` pairs.

    Args:
        outcome: `LabelLowering.review_pending`'s counts.

    Returns:
        One line: lowered, rejected, unanswered and handed to the human.
    """
    return (
        f"lowered={outcome.lowered}  rejected={outcome.rejected}  "
        f"unanswered={outcome.unanswered}  handed_to_human={outcome.handed_to_human}"
    )


def print_decision(proposal: LoweringProposal) -> None:
    """Print the human's decision as the store recorded it: lowered, or rejected and why.

    Args:
        proposal: The proposal as decided: LOWERED, or REJECTED (a denial, or an approval whose
            target no longer stood, which its note says).
    """
    approver = proposal.approver.value if proposal.approver is not None else _NONE
    # Lowered: the Nectar and every Honey row still at the old label now carry the target.
    if proposal.state is LoweringState.LOWERED:
        typer.echo(
            f"lowered: {proposal.id} {proposal.from_label.value} -> {proposal.to_label.value} "
            f"(approver {approver})"
        )
        return
    note = f"; {proposal.note}" if proposal.note else ""
    typer.echo(
        f"rejected: {proposal.id} stays {proposal.from_label.value} (approver {approver}){note}"
    )


def print_prune(outcome: PruneOutcome) -> None:
    """Print what a prune dropped, per model, or why it refused and deleted nothing (stderr).

    Args:
        outcome: `hivemind.honey_store.prune_vectors`'s result.
    """
    # Refused: some live row still lacks a vector for the kept model, so nothing was deleted.
    if outcome.refused:
        typer.echo(
            f"prune refused: {outcome.missing} live row(s) still lack a {outcome.kept_model} "
            "vector; nothing was deleted",
            err=True,
        )
        return
    if not outcome.dropped:
        typer.echo(f"pruned nothing: every stored vector is already {outcome.kept_model}'s")
        return
    typer.echo(f"pruned every embedding model but {outcome.kept_model}:")
    for model, rows in sorted(outcome.dropped.items()):
        typer.echo(f"{_INDENT}{model}: {rows} vectors dropped")


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


def _source(source: NectarSource) -> str:
    """Render one extra depositor: where and when it came from and what it declared; no content."""
    return (
        f"task={source.task_id or _NONE}  cell={source.cell_id}  bee={source.bee or _NONE}  "
        f"observed={source.observed_at.isoformat()}  origin={source.origin.value}  "
        f"tier={source.origin_tier.value}  declared={source.clearance.value}  "
        f"key={source.source_key or _NONE}"
    )


def _proposal_details(proposal: LoweringProposal) -> tuple[tuple[str, str], ...]:
    """Return a proposal's detail lines: both reasons always; decision and note when present."""
    details = [
        ("ripener reason", proposal.ripener_reason or _NONE),
        ("judge reasons", "; ".join(proposal.verdict_reasons) or _NONE),
    ]
    # Decided: who decided it and when (under which rubric, for the judge), and the human's own
    # reason when it was theirs.
    if proposal.approver is not None:
        decided_at = proposal.decided_at.isoformat() if proposal.decided_at else _NONE
        rubric = f" (rubric {proposal.rubric_id})" if proposal.rubric_id else ""
        details.append(("decided", f"{proposal.approver.value} at {decided_at}{rubric}"))
    if proposal.human_reason:
        details.append(("human reason", proposal.human_reason))
    # Why it waits for the human, or why it was rejected unasked.
    if proposal.note:
        details.append(("note", proposal.note))
    return tuple(details)
