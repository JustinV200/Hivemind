"""Refuse to resume a bee from a Handoff this Cell's own store labels tainted.

ADR-0035: a tainted item never reaches a prompt or a resumed bee. The Hive's own loaders refuse one
outright (`hivemind.memory.read_handoff`), and so does a Worker's first read of its resume
Handoff; this is the Warden's half (roadmap step 10.6a), in front of every `TaskAssign` before
anything spawns, the way its quarantine gate stands in front of a quarantined task's. An
assignment that resumes from a Handoff this Cell's store labels TAINTED (an isolation's order
labelled it, `hivemind.wardens.isolation.taint`) is refused: a `guard.denied` row at the
`isolation` point, rule `guard.scope.tainted_handoff`; the task's grant withdrawn, so nothing
spawns under it; and the Queen told the task is held, since her resume moved it to RUNNING before
the Warden could say no. So a lift, which restores the Cell's placement and egress, never restores
a resume from what the isolation tainted: only a judge's clearance does (`memory.taint_cleared`).
A fresh start, a Handoff the store does not hold, or one only over the task's clearance is none
of this gate's business: the bee's own loader answers those exactly as it always has.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    isolation sub-package. Called by `hivemind.wardens.warden.Warden`'s tick for every
    `TaskAssign`, after the quarantine gate and before `hivemind.wardens.ticks.assign.
    handle_assign`. Calls into `hivemind.cell` (HoneyClearance), `hivemind.guard`,
    `hivemind.memory` (read_handoff and its errors) and waggle only.

Key invariants:
    - A resume from a TAINTED Handoff never spawns; every other assignment is admitted unchanged.
    - A refusal changes nothing but its `guard.denied` row, the task's grant and the Queen's view.

See Also:
    - hivemind.wardens.quarantine.gate for the same refusal of a quarantined task's resume.
    - docs/guard/tainted-memory.md for the label and its one clearer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.guard import (
    QUEEN_ROLE,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    queen_principal,
    role_set,
)
from hivemind.memory import ClearanceError, HandoffNotFoundError, TaintedMemoryError, read_handoff
from waggle.envelope import wrap
from waggle.messages.task import TaskAssign, TaskProgress, TaskStage
from waggle.messages.task.reports import MAX_SUMMARY_CHARS

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

TAINTED_HANDOFF_SCOPE = "tainted_handoff"  # The refusal's rule: guard.scope.tainted_handoff.

__all__ = ["TAINTED_HANDOFF_SCOPE", "admit_resume"]


async def admit_resume(warden: Warden, assignment: TaskAssign) -> bool:
    """Return whether `assignment` may spawn; refuse a resume from a tainted Handoff.

    Args:
        warden: The Warden the assignment reached; its store is read, and on a refusal its
            grants and pending assignments are written.
        assignment: The Queen's TaskAssign.

    Returns:
        False, with the refusal on the trail and the task held, when it resumes from a Handoff
        this Cell's store labels TAINTED; True otherwise.
    """
    ref = assignment.resume_from
    if ref is None:
        return True  # A fresh start reads no memory: nothing to refuse.
    allowance = HoneyClearance.from_wire(assignment.clearance)
    try:
        await read_handoff(warden._deps.memory, ref, allowance)
    except TaintedMemoryError as tainted:
        why = (
            f"task {assignment.task_id} resumes from Handoff {tainted.item_id}, which "
            f"{tainted.event_id} labelled tainted; only a judge's clearance lets it resume"
        )
        await _refuse(warden, assignment, why)
        return False
    except (ClearanceError, HandoffNotFoundError):
        return True  # Not a taint: the bee's own loader answers these, exactly as before.
    return True


async def _refuse(warden: Warden, assignment: TaskAssign, why: str) -> None:
    """Record the refusal, withdraw the task's grant, and tell the Queen the task is held."""
    deps = warden._deps
    # A TaskAssign is the Queen's alone, so she is the principal the refusal names.
    request = PolicyRequest(
        principal=queen_principal(deps.identity.hive_id),
        point=EnforcementPoint.ISOLATION,
        needed=deps.lease_capability,
        held=role_set(deps.guard, QUEEN_ROLE),
        context=_context(warden),
    )
    await deps.enforcer.refuse(request, TAINTED_HANDOFF_SCOPE, why)
    # Nothing may spawn under the task's grant, nor from an assignment parked for it.
    grant = warden._grants.get(assignment.grant_id)
    if grant is not None and grant.task_id == assignment.task_id:
        del warden._grants[assignment.grant_id]
    warden._pending.pop(assignment.task_id, None)
    progress = TaskProgress(
        task_id=assignment.task_id,
        attempt=assignment.attempt,
        stage=TaskStage.PAUSED,
        summary=f"Held: {why}."[:MAX_SUMMARY_CHARS],
        clearance=assignment.clearance,
        fraction_done=None,
        handoff=None,
    )
    await deps.queen_link.send(wrap(progress, deps.hop, clock=deps.clock))


def _context(warden: Warden) -> PolicyContext:
    """Return the Cell's tier and access level, or an empty context before any lease."""
    cell = warden._cell
    if cell is None:
        return PolicyContext()
    return PolicyContext(comb_shield=cell.comb_shield, access_level=cell.access_level)
