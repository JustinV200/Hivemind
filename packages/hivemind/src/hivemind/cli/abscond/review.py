"""Render an Absconding's scope review, read the Leavings the human ticks, and print the receipt.

Roadmap step 13.4: before anything is torn down the human sees everything that will be, counted
and named, and every Leaving (a path left outside scratch on purpose, often at their own word)
numbered from 1, kept unless they tick it. `review_lines` is that review; `parse_ticks` reads the
numbers they typed (comma- or space-separated; blank keeps every Leaving) and refuses anything
that is not one of them, so a typo never removes the wrong path; `receipt_lines` says what
happened.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.abscond`. Called by
    `.command`. Calls into the package's own scope and execute, and `hivemind.cell.leavings`.

Key invariants:
    - Pure: no I/O; the command prints and prompts.
    - Ids, paths and counts only: never a Leaving's prior bytes (codingrules 12).

See Also:
    - hivemind.cli.abscond.command for the order the review, the ticks and the phrase come in.
"""

from __future__ import annotations

from hivemind.cell.leavings import Leaving
from hivemind.cli.abscond.execute import AbscondReceipt
from hivemind.cli.abscond.scope import AbscondScope

__all__ = ["parse_ticks", "receipt_lines", "review_lines"]


def review_lines(scope: AbscondScope) -> list[str]:
    """Return the scope review: what goes, then every Leaving numbered for ticking.

    Args:
        scope: What the Absconding would tear down.

    Returns:
        The review, one line each.
    """
    deferred = " (a Queen looks to be running: hers are released by RELEASE orders)"
    lines = [
        "This Absconding tears down, from backend labels and the trail:",
        f"  Virtual Cells: {len(scope.cells)} ({scope.dormant} dormant)",
        f"  Hive Stand leases: {len(scope.stand_leases)}{deferred if scope.queen_running else ''}",
        f"  device leases: {len(scope.device_leases)} (released by each device's Warden)",
        f"  live grants: {len(scope.grant_ids)}",
        f"  Workers still running: {len(scope.workers)} (they end with their lease)",
        f"  temp dirs: {len(scope.temp_dirs)}",
        f"  resident Basket blobs: {len(scope.resident)}",
        "The trail, the Brood Chamber, Honey and the ledger are kept.",
    ]
    if not scope.leavings:
        return [*lines, "No Leavings were left."]
    lines.append("Leavings, kept unless you tick them:")
    for number, leaving in enumerate(scope.leavings, start=1):
        lines.append(f"  [{number}] {leaving.path} (Cell {leaving.cell_id}, {_who(leaving)})")
    return lines


def parse_ticks(typed: str, leavings: tuple[Leaving, ...]) -> tuple[Leaving, ...]:
    """Return the Leavings whose numbers `typed` names; blank names none.

    Args:
        typed: What the human typed, e.g. "1, 3".
        leavings: The Leavings in review order.

    Returns:
        The ticked Leavings, each once, in review order.

    Raises:
        ValueError: A token is not the number of a listed Leaving.
    """
    chosen: set[int] = set()
    for token in typed.replace(",", " ").split():
        if not token.isdigit() or not 1 <= int(token) <= len(leavings):
            raise ValueError(f"{token!r} is not the number of a listed Leaving.")
        chosen.add(int(token))
    return tuple(leaving for n, leaving in enumerate(leavings, start=1) if n in chosen)


def receipt_lines(receipt: AbscondReceipt) -> list[str]:
    """Return what the Absconding did, as the operator reads it.

    Args:
        receipt: What it did.

    Returns:
        The receipt, one line each.
    """
    summary = receipt.summary
    return [
        f"Absconded (guard.absconded {receipt.event_id}).",
        f"  containers destroyed: {summary.containers_destroyed}",
        f"  leases released: {summary.leases_released}; deferred to a Queen: "
        f"{summary.leases_deferred_to_queen}; left (scratch already gone): "
        f"{summary.leases_left_untouched}",
        f"  grants revoked: {summary.grants_revoked}",
        f"  temp dirs removed: {receipt.temp_dirs_removed}",
        f"  resident blobs evicted: {receipt.resident_evicted}",
        f"  Leavings removed: {receipt.leavings_removed}",
        f"  left as found: {summary.left_as_found}",
    ]


def _who(leaving: Leaving) -> str:
    """Who let it stay, as the review names it."""
    return f"left by {leaving.approved_by.value}"
