"""Define Placement and decide: the Queen's pure choice of which Warden's Cell runs a task.

Codingrules section 8.7: "Placement is a pure decision... maps TaskNeeds plus the current Cell
inventory... to a Placement: reuse this Real Cell, or provision a Virtual Cell from this spec. The
reason is recorded on the trail." v0 has no Virtual Cell provisioner and exactly one kind of
candidate: the attached Wardens, each already owning one Real Cell (the Hive Stand, in practice, per
roadmap step 3.20's own bullet). `decide` therefore reduces to checks over the attached
`hivemind.queen.deps.WardenLink`s, in attachment order -- `needs.isolation == Isolation.REQUIRED`
can never be satisfied by any Real Cell (codingrules section 15: "a task with isolation = 'required'
never lands on a Real Cell"), `needs.os`, when set, must match a candidate's own
`capabilities.os` -- both a `TaskNeeds` check, never a branch on `cell.kind` (`CellCapabilities`
carries no isolation field of its own yet, so this is entirely about what the task asks for, not
what the Cell claims to be) -- and, roadmap step 4.2a, a candidate whose Cell carries a WRITTEN
`WaxSeverity.BLOCK` note is excluded outright, while one carrying a WRITTEN `CAUTION` is kept but
ranked behind every clean candidate. `blocked_cells`/`cautioned_cells` are precomputed by the
caller (from `hivemind.memory.cell_wax.CellWax.state`/`.severity`), not read from a store here:
`decide` stays pure, with no I/O of its own, matching codingrules 8.7 exactly.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    placement sub-package. Called once per ready task by `hivemind.queen.dispatcher.
    dispatch_ready`. Calls into `hivemind.cell` (Isolation, TaskNeeds), `hivemind.queen.deps`
    (WardenLink), `hivemind.queen.errors` (QueenError) and waggle only.

Key invariants:
    - `decide` is pure: given the same arguments, it always returns the same `Placement` or raises
      the same shape of `PlacementError`.
    - `decide` never reads `warden.cell.kind`: every check here is a `TaskNeeds` comparison against
      `needs.isolation` or `warden.cell.capabilities`, or a Cell Wax set membership check, never a
      `cell.kind` branch (`scripts/check_no_kind_branches.py` allowlists this module for exactly
      that reason).
    - Among candidates that fit and are not BLOCKED, an uncautioned one always outranks a CAUTIONed
      one, but attachment order still breaks every other tie: `blocked_cells`/`cautioned_cells`
      only ever narrow or reorder v0's single-Warden default, never replace it with a different
      kind of ranking.

See Also:
    - .claude/codingrules.md section 8.7 for "placement is a pure decision" and the inputs it maps.
    - .claude/codingrules.md section 15 for "a task with isolation = 'required' never lands on a
      Real Cell".
    - docs/adr/0019-queen-kernel-autopilot-first-with-stateless-awake-episodes.md for "placement v0
      is the Hive Stand only".
    - .claude/roadmap.md step 4.2a for "placement treats BLOCK as exclusion and CAUTION as a
      penalty".
    - hivemind.queen.dispatcher for dispatch_ready, this function's one caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import Isolation, TaskNeeds
from hivemind.queen.deps import WardenLink
from hivemind.queen.errors import QueenError
from waggle.ids import CellId, WardenId

__all__ = ["Placement", "PlacementError", "decide"]


class Placement(BaseModel):
    """Where a task lands: which Cell, and the Warden that owns it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_id: CellId = Field(description="The Cell placement chose.")
    warden_id: WardenId = Field(description="The Warden that owns that Cell.")


class PlacementError(QueenError):
    """Raise when no attached Warden's Cell can satisfy a task's TaskNeeds.

    Roots at `hivemind.queen.errors.QueenError`, not `hivemind.common.errors.NotFoundError`: the
    Hive Stand exists and is attached; what is missing is a Cell that *fits*, not a Cell at all.
    """

    code: ClassVar[str] = "hivemind.queen.placement_error"


def decide(
    needs: TaskNeeds,
    wardens: Sequence[WardenLink],
    *,
    blocked_cells: frozenset[CellId] = frozenset(),
    cautioned_cells: frozenset[CellId] = frozenset(),
) -> Placement:
    """Choose which attached Warden's Cell runs a task with `needs`.

    Args:
        needs: What the task requires from its Cell.
        wardens: Every Warden currently attached to the Queen, in attachment order.
        blocked_cells: Cells carrying a WRITTEN `WaxSeverity.BLOCK` note (roadmap step 4.2a);
            excluded outright, never a candidate regardless of fit. Empty by default, so every
            existing caller sees v0's old behaviour unchanged.
        cautioned_cells: Cells carrying a WRITTEN `WaxSeverity.CAUTION` note; still a candidate,
            but ranked behind every candidate not in this set.

    Returns:
        A Placement naming the highest-ranked fitting, non-BLOCKED candidate's own Cell: among
        wardens whose Cell fits `needs` and is not `blocked_cells`, an uncautioned one before a
        cautioned one, attachment order breaking every other tie (v0 places every task on the
        Hive Stand when it is the only candidate, exactly as before this step).

    Raises:
        PlacementError: No Warden is attached, `needs.isolation` is `Isolation.REQUIRED` (no Cell
            can isolate in v0: `CellCapabilities` carries no isolation field yet), or no attached
            Warden's Cell both fits `needs` and is outside `blocked_cells`.
    """
    if not wardens:
        raise PlacementError("No Warden is attached; there is no Cell to place a task on.")
    if needs.isolation is Isolation.REQUIRED:
        raise PlacementError(
            "TaskNeeds.isolation is REQUIRED, but no Cell can isolate in v0 (no Virtual Cell "
            "provisioner exists yet); this task can never be placed."
        )
    candidates = [
        link for link in wardens if _fits_os(needs, link) and link.cell.id not in blocked_cells
    ]
    if not candidates:
        raise PlacementError(
            f"No attached Warden's Cell both fits {needs!r} and is free of a BLOCK Cell Wax note "
            f"(checked {len(wardens)} attached Warden(s))."
        )
    # Stable sort: an uncautioned candidate outranks a cautioned one (roadmap step 4.2a's own
    # "CAUTION is a penalty in ordering"), and attachment order still breaks every other tie,
    # so v0's single-Warden case picks exactly the Cell it always did.
    ranked = sorted(candidates, key=lambda link: link.cell.id in cautioned_cells)
    warden = ranked[0]
    return Placement(cell_id=warden.cell.id, warden_id=warden.warden_id)


def _fits_os(needs: TaskNeeds, link: WardenLink) -> bool:
    """Return whether `link`'s own Cell satisfies `needs.os` (None fits any Cell)."""
    return needs.os is None or needs.os is link.cell.capabilities.os
