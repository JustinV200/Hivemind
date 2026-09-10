"""Define Placement and decide: the Queen's pure choice of which Warden's Cell runs a task.

Codingrules section 8.7: "Placement is a pure decision... maps TaskNeeds plus the current Cell
inventory... to a Placement: reuse this Real Cell, or provision a Virtual Cell from this spec. The
reason is recorded on the trail." v0 has no Virtual Cell provisioner and exactly one kind of
candidate: the attached Wardens, each already owning one Real Cell (the Hive Stand, in practice, per
roadmap step 3.20's own bullet). `decide` therefore reduces to two checks over the first attached
`hivemind.queen.deps.WardenLink` -- `needs.isolation == Isolation.REQUIRED` can never be satisfied
by any Real Cell (codingrules section 15: "a task with isolation = 'required' never lands on a Real
Cell"), and `needs.os`, when set, must match that Cell's own `capabilities.os` -- both a `TaskNeeds`
check, never a branch on `cell.kind` (`CellCapabilities` carries no isolation field of its own yet,
so this is entirely about what the task asks for, not what the Cell claims to be).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    placement sub-package. Called once per ready task by `hivemind.queen.dispatcher.
    dispatch_ready`. Calls into `hivemind.cell` (Isolation, TaskNeeds), `hivemind.queen.deps`
    (WardenLink), `hivemind.queen.errors` (QueenError) and waggle only.

Key invariants:
    - `decide` is pure: given the same `(needs, wardens)`, it always returns the same `Placement`
      or raises the same shape of `PlacementError`.
    - `decide` never reads `warden.cell.kind`: every check here is a `TaskNeeds` comparison against
      `needs.isolation` or `warden.cell.capabilities`, never a `cell.kind` branch
      (`scripts/check_no_kind_branches.py` allowlists this module for exactly that reason).
    - v0 always picks `wardens[0]` -- the first attached Warden, the Hive Stand -- once it clears
      both checks; a later roadmap phase adds real ranking over several candidates.

See Also:
    - .claude/codingrules.md section 8.7 for "placement is a pure decision" and the inputs it maps.
    - .claude/codingrules.md section 15 for "a task with isolation = 'required' never lands on a
      Real Cell".
    - docs/adr/0019-queen-kernel-autopilot-first-with-stateless-awake-episodes.md for "placement v0
      is the Hive Stand only".
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


def decide(needs: TaskNeeds, wardens: Sequence[WardenLink]) -> Placement:
    """Choose which attached Warden's Cell runs a task with `needs`.

    Args:
        needs: What the task requires from its Cell.
        wardens: Every Warden currently attached to the Queen, in attachment order.

    Returns:
        A Placement naming `wardens[0]`'s own Cell -- v0 places every task on the Hive Stand.

    Raises:
        PlacementError: No Warden is attached, `needs.isolation` is `Isolation.REQUIRED` (no Cell
            can isolate in v0: `CellCapabilities` carries no isolation field yet), or `needs.os` is
            set and does not match the first attached Warden's own Cell.
    """
    if not wardens:
        raise PlacementError("No Warden is attached; there is no Cell to place a task on.")
    if needs.isolation is Isolation.REQUIRED:
        raise PlacementError(
            "TaskNeeds.isolation is REQUIRED, but no Cell can isolate in v0 (no Virtual Cell "
            "provisioner exists yet); this task can never be placed."
        )
    warden = wardens[0]  # v0: the first attached Warden, the Hive Stand.
    if needs.os is not None and needs.os is not warden.cell.capabilities.os:
        raise PlacementError(
            f"TaskNeeds.os is {needs.os.name}, but the Hive Stand's own Cell "
            f"{warden.cell.id!r} runs {warden.cell.capabilities.os.name}."
        )
    return Placement(cell_id=warden.cell.id, warden_id=warden.warden_id)
