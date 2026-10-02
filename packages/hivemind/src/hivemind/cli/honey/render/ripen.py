"""Print what `hive honey ripen` found or did: what is waiting, or the totals of `--now`'s passes.

`RipenTotals` is the shape `ripen --now` sums its House Bee passes into: notes drained, the
Ripener's counts, re-embedded rows, lowering proposals filed and what the judge decided, plus how
many operator notes are still queued afterwards and whether the run stopped at its pass bound
(phase 7 handoff item 7: it used to stop without saying so). `WaitingCounts` is what plain
`ripen` reports. Each prints as text, or as one JSON object for `--json`.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.honey.render`. Re-exported
    by the package's face. Calls into `hivemind.honey_store` (RipenOutcome, ReviewOutcome) and
    this package's `review` (review_counts) only.

Key invariants:
    - A queued-note count that hit its read cap is printed as "at least N", never as exactly N.
    - Notes still queued after `--now` are always printed, with why: the pass bound (run again)
      or refused notes the House Bee passed over (see the log).

See Also:
    - hivemind.cli.honey.maintain for `ripen`, which builds these.
    - hivemind.workers.roles.house_bee.loop for the pass each `--now` run repeats.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import typer

from hivemind.cli.honey.render.review import review_counts
from hivemind.honey_store import ReviewOutcome, RipenOutcome

__all__ = ["RipenTotals", "WaitingCounts", "print_ripen", "print_waiting", "ripen_json"]


@dataclass(frozen=True, slots=True)
class WaitingCounts:
    """How much plain `ripen` (without `--now`) found waiting for the House Bee."""

    nectar: int  # RECEIVED Nectar, not yet ripened.
    notes: int  # Operator notes still queued, counted up to the read cap.
    notes_capped: bool  # The count hit its read cap: at least `notes` are queued.
    interval_s: float  # How often a running Hive's House Bee runs its pass.


@dataclass(frozen=True, slots=True)
class RipenTotals:
    """What every House Bee pass one `ripen --now` ran did, summed, and what is still queued."""

    passes: int  # Whole House Bee passes run.
    drained: int  # Operator notes turned into Nectar.
    ripen: RipenOutcome  # The Ripener's counts across every pass.
    reembedded: int  # Older rows given a vector for the current embedder.
    filed: int  # Label lowering proposals filed (ADR-0038).
    review: ReviewOutcome  # What the clearance judge decided.
    notes_left: int  # Operator notes still queued afterwards, counted up to the read cap.
    notes_left_capped: bool  # That count hit its read cap: at least `notes_left` remain.
    hit_pass_cap: bool  # Stopped at the pass bound with a full batch still draining.


def print_waiting(counts: WaitingCounts) -> None:
    """Print how much is waiting and when a running Hive's House Bee acts on it."""
    typer.echo(
        f"{counts.nectar} Nectar waiting to ripen, {_notes(counts.notes, counts.notes_capped)} "
        f"operator note(s) queued; a running Hive's House Bee ripens and drains notes every "
        f"{counts.interval_s:g}s. Pass --now to run it here."
    )


def print_ripen(totals: RipenTotals) -> None:
    """Print `ripen --now`'s totals, then any notes still queued and why they still are."""
    ripen = totals.ripen
    typer.echo(f"drained {totals.drained} operator note(s) in {totals.passes} pass(es)")
    typer.echo(
        f"ripened {ripen.ripened} Nectar into {ripen.rows} Honey rows "
        f"(failed={ripen.failed}  discarded={ripen.discarded}  deduplicated={ripen.deduped}  "
        f"embedded={ripen.embedded}); re-embedded {totals.reembedded} older rows"
    )
    # ADR-0038: what the passes proposed, and what the judge decided about waiting proposals.
    typer.echo(
        f"filed {totals.filed} label lowering proposal(s); judge review: "
        f"{review_counts(totals.review)}"
    )
    # Never stop silently: say how many notes remain and whether running again would drain them.
    if totals.notes_left == 0:
        return
    left = _notes(totals.notes_left, totals.notes_left_capped)
    if totals.hit_pass_cap:
        typer.echo(
            f"{left} operator note(s) still queued: stopped at the {totals.passes}-pass bound; "
            "run `hive honey ripen --now` again to drain more"
        )
        return
    typer.echo(f"{left} operator note(s) still queued: refused and passed over (see the log)")


def ripen_json(result: RipenTotals | WaitingCounts) -> str:
    """Return `ripen`'s result, waiting counts or `--now`'s totals, as one JSON object."""
    return json.dumps(asdict(result), indent=2)


def _notes(count: int, capped: bool) -> str:
    """Render a queued-note count, as "at least N" when it hit its read cap."""
    return f"at least {count}" if capped else str(count)
