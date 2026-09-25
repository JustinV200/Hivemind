"""Let a quarantined task out only by the one way ADR-0043 allows: a judge-cleared checkpoint.

"The only way out is a respawn from a Handoff the judge has cleared" (ADR-0043, roadmap step
10.6c). While a task is quarantined its Warden (the supervisor of its Cell) holds a
`QuarantineRecord` for it, and `admit_respawn` stands in front of every `TaskAssign` for that task
before anything spawns: it is admitted only when it resumes from the record's own checkpoint (the
Handoff the quarantine wrote before cutting anything) and that Handoff now carries a CLEARED label,
which only `hivemind.memory.taint.clear_taint`, on a judge's verdict, ever writes. The Handoff is
read through `hivemind.memory.read_handoff`, the loader every resume goes through, so a Handoff
still tainted is refused exactly where every other one is. An admitted respawn lifts the record,
and the new sub-bee resumes from the cleared Handoff as any resume does. A refused one spawns
nothing: its refusal is a `guard.denied` row at the `quarantine` point, its grant is withdrawn like
the quarantined bee's own, and the Queen is told again that the task is held, since her resume
moved it back to RUNNING before the Warden could say no.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    quarantine sub-package. Called by `hivemind.wardens.warden.Warden`'s tick for every
    `TaskAssign`, ahead of `hivemind.wardens.ticks.assign.handle_assign`. Calls into
    `hivemind.cell` (HoneyClearance), `hivemind.memory` (read_handoff and its errors),
    `hivemind.memory.taint` (TaintState), this package's `authority`, `record` and `report`, and
    waggle only.

Key invariants:
    - An assignment for a task with no record is always admitted, unread and unchanged.
    - A record is lifted only by an assignment resuming from its own checkpoint, labelled CLEARED:
      an unlabelled or still-tainted checkpoint, another Handoff, or none at all, is refused.

See Also:
    - docs/guard/tainted-memory.md, "Who clears it".
    - hivemind.wardens.quarantine.path for the quarantine that writes the record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.memory import ClearanceError, HandoffNotFoundError, TaintedMemoryError, read_handoff
from hivemind.memory.taint import TaintState
from hivemind.wardens.quarantine.authority import refuse_respawn
from hivemind.wardens.quarantine.record import QuarantineRecord
from hivemind.wardens.quarantine.report import report_paused
from waggle.messages.task import TaskAssign

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["admit_respawn"]


async def admit_respawn(warden: Warden, assignment: TaskAssign) -> bool:
    """Return whether `assignment` may spawn; refuse a quarantined task's uncleared respawn.

    Args:
        warden: The Warden the assignment reached; its `_quarantined` table is read and, on an
            admitted way out, written.
        assignment: The Queen's TaskAssign.

    Returns:
        True for a task this Warden holds no quarantine for, and for the one way out (which lifts
        the quarantine); False, with the refusal on the trail and the task held, otherwise.
    """
    record = warden._quarantined.get(assignment.task_id)
    if record is None:
        return True  # Not quarantined here: nothing to hold.
    why = await _refusal(warden, record, assignment)
    if why is None:
        # The one way out: the checkpoint the quarantine wrote, cleared by a judge.
        del warden._quarantined[assignment.task_id]
        return True
    await refuse_respawn(warden, why)
    # Its grant may not be spawned under either, and the Queen's resume must not stand.
    grant = warden._grants.get(assignment.grant_id)
    if grant is not None and grant.task_id == assignment.task_id:
        del warden._grants[assignment.grant_id]
    await report_paused(warden, record, assignment.attempt)
    return False


async def _refusal(warden: Warden, record: QuarantineRecord, assignment: TaskAssign) -> str | None:
    """Return why `assignment` may not let its task out of quarantine, or None when it may."""
    checkpoint = record.checkpoint.event_id
    ref = assignment.resume_from
    if ref is None or ref.event_id != checkpoint:
        return f"task {assignment.task_id} is quarantined and resumes only from {checkpoint}"
    allowance = HoneyClearance.from_wire(assignment.clearance)
    try:
        handoff = await read_handoff(warden._deps.memory, ref, allowance)
    except TaintedMemoryError:
        return f"checkpoint {checkpoint} is still tainted; no judge has cleared it"
    except (ClearanceError, HandoffNotFoundError) as refused:
        return f"checkpoint {checkpoint} cannot be resumed from ({refused.code})"
    # Unlabelled would mean the quarantine never tainted it; only a judge's CLEARED lets it out.
    if handoff.tainted is None or handoff.tainted.state is not TaintState.CLEARED:
        return f"checkpoint {checkpoint} carries no judge's clearance"
    return None
