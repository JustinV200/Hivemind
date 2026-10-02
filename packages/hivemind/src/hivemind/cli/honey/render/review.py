"""Print label lowering proposals (ADR-0038): `hive honey review`'s listing, decisions and counts.

`ReviewEntry` is the one shape defined in this package: a proposal beside the title and browser
path `hive honey review` looked up for it. This module prints a listing of them (or one JSON
document for `--json`), one proposal with its reasons and decision, what a judge review step did,
and the human's decision as the store recorded it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.honey.render`. Re-exported
    by the package's face. Calls into `hivemind.honey_store` (LoweringProposal, LoweringState,
    ReviewOutcome) and this package's `common` only.

Key invariants:
    - Nothing here reads a store or decides what a reader may see; a withheld title arrives as
      None and is printed as withheld.
    - Every `--json` document is one JSON value on stdout.

See Also:
    - hivemind.cli.honey.review for the commands that print through this module.
    - hivemind.honey_store.lowering for LoweringProposal and ReviewOutcome.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import typer

from hivemind.cli.honey.render.common import INDENT, NONE
from hivemind.honey_store import LoweringProposal, LoweringState, ReviewOutcome

_WITHHELD = "(title above --clearance)"  # A proposal's title the reader's ceiling hides.
_DECIDE_HINT = "decide one with: hive honey review approve|deny <id> --reason TEXT"

__all__ = [
    "ReviewEntry",
    "print_decision",
    "print_review",
    "print_review_entry",
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
    title = _WITHHELD if entry.title is None else (entry.title or NONE)
    typer.echo(
        f"{proposal.state.value:<8}  {proposal.id}  {proposal.from_label.value} -> "
        f"{proposal.to_label.value}  attempts={proposal.attempts}  {entry.path or NONE}  {title}"
    )
    for name, value in _proposal_details(proposal):
        typer.echo(f"{INDENT}{name}: {value}")


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
    approver = proposal.approver.value if proposal.approver is not None else NONE
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


def _proposal_details(proposal: LoweringProposal) -> tuple[tuple[str, str], ...]:
    """Return a proposal's detail lines: both reasons always; decision and note when present."""
    details = [
        ("ripener reason", proposal.ripener_reason or NONE),
        ("judge reasons", "; ".join(proposal.verdict_reasons) or NONE),
    ]
    # Decided: who decided it and when (under which rubric, for the judge), and the human's own
    # reason when it was theirs.
    if proposal.approver is not None:
        decided_at = proposal.decided_at.isoformat() if proposal.decided_at else NONE
        rubric = f" (rubric {proposal.rubric_id})" if proposal.rubric_id else ""
        details.append(("decided", f"{proposal.approver.value} at {decided_at}{rubric}"))
    if proposal.human_reason:
        details.append(("human reason", proposal.human_reason))
    # Why it waits for the human, or why it was rejected unasked.
    if proposal.note:
        details.append(("note", proposal.note))
    return tuple(details)
