"""Print what a query and the store's own upkeep return: hits, counts, a slot's status, a prune.

`hive honey query` prints a `HoneyResponse`'s hits, `stats` prints `HoneyStats`, and the commands
that call a model (`query`, `ripen --now`, `reembed`, `review --judge`) print how each model slot
resolved; `reembed --prune` prints what a prune dropped. Retrieved text is shown as reference data,
never run: a hit's excerpt is printed, nothing more (codingrules section 15, "Nothing is executed
from the Honey Store").

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.honey.render`. Re-exported
    by the package's face. Calls into `hivemind.honey_store` (the shapes it prints),
    `hivemind.cli.honey.context` (SlotStatus) and this package's `common` only.

Key invariants:
    - Nothing here reads a store; it prints what it is given.
    - A refused prune is printed to stderr, since the command then exits 1.

See Also:
    - waggle.messages.honey for HoneyResponse and HoneyHit.
    - hivemind.honey_store.ripening for prune_vectors and PruneOutcome.
"""

from __future__ import annotations

import typer

from hivemind.cli.honey.context import SlotStatus
from hivemind.cli.honey.render.common import INDENT, counts, provenance
from hivemind.honey_store import HoneyStats, PruneOutcome
from waggle.messages.honey import HoneyResponse

__all__ = ["print_prune", "print_response", "print_slot", "print_stats"]


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
        typer.echo(f"{INDENT}{provenance(hit.provenance)}")
        for line in hit.excerpt.splitlines():
            typer.echo(f"{INDENT}{line}")
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
        f"nectar:     {counts({state.value: n for state, n in stats.nectar_by_state.items()})}"
    )
    typer.echo(f"honey:      {counts({part.value: n for part, n in stats.honey_by_part.items()})}")
    typer.echo(f"            tainted={stats.honey_tainted}  retired={stats.honey_retired}")
    typer.echo(
        f"clearance:  {counts({label.value: n for label, n in stats.honey_by_clearance.items()})}"
    )
    typer.echo(f"scopes:     {counts(stats.honey_by_scope_kind)}")
    # ADR-0036: coverage per embedding model, so a re-embed's progress is visible.
    typer.echo(f"vectors:    {counts(stats.vectors_by_model)}")
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
        typer.echo(f"{INDENT}{model}: {rows} vectors dropped")
