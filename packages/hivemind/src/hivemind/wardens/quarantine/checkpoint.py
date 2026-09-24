"""Write a quarantined bee's checkpoint: the Handoff written before anything is cut.

Codingrules Appendix C, rule 1: in-flight work is covered by a Handoff on every intervention, and
ADR-0035's quarantine (roadmap step 10.6c) starts with exactly that: "checkpoint, cancel, kill".
The bee may be compromised, so its Warden (the supervisor of its Cell) does not ask it to write
one: a bee that would not comply, or would take its time, must not delay being stopped. The
Warden composes the Handoff itself from what it holds: the bee's own last Handoff when it can
still read one (carried forward, so no progress is thrown away), otherwise the task's objective
and the bee's last reported actions; either way with a note naming the quarantine. It is written
through `hivemind.memory.write_checkpoint` like every other checkpoint (a `memory.checkpoint`
event and its Bee Bread index entry), and, written after the suspect episode, it is tainted with
the rest of the bee's memory a moment later: the only way out of quarantine is a respawn from
this Handoff once a judge has cleared it.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    quarantine sub-package. Called by `hivemind.wardens.quarantine.path` first of every step.
    Calls into `hivemind.cell` (HoneyClearance), `hivemind.memory` (Handoff, read_handoff,
    write_checkpoint, the memory context and errors) and waggle only.

Key invariants:
    - The checkpoint is written before the bee is stopped, so it always exists by the time the
      bee is gone (a test asserts the order).
    - Nothing here copies a taint label: the carried-forward Handoff is rebuilt field by field
      without its marker, and a Handoff already refused is not carried forward at all.
    - Its text names ids only beyond what the bee itself wrote; it never quotes a prompt.

See Also:
    - hivemind.memory.checkpoint for write_checkpoint and read_handoff.
    - hivemind.wardens.quarantine.gate for the respawn that resumes from it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.memory import (
    ClearanceError,
    Handoff,
    HandoffNotFoundError,
    MemoryContext,
    TaintedMemoryError,
    read_handoff,
    write_checkpoint,
)
from hivemind.memory.handoff import (
    MAX_GOAL_CHARS,
    MAX_LIST_ITEM_CHARS,
    MAX_NOTES_CHARS,
    MAX_PROGRESS_CHARS,
)
from hivemind.wardens.quarantine.order import QuarantineOrder
from waggle.messages import HandoffRef

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

NO_PROGRESS = "No progress was reported before the quarantine."  # When nothing was reported.
RESUME_STEP = "Resume only once a judge has cleared this checkpoint."  # The one next step.

__all__ = ["NO_PROGRESS", "RESUME_STEP", "memory_context", "write_quarantine_checkpoint"]


async def write_quarantine_checkpoint(
    warden: Warden, sub_bee: SubBee, order: QuarantineOrder
) -> HandoffRef:
    """Compose and write the quarantined bee's Handoff, before anything is cut.

    Args:
        warden: The Warden carrying the quarantine out; its memory store takes the Handoff.
        sub_bee: The bee being quarantined, still running.
        order: The quarantine order; its suspect episode and orderer are named in the notes.

    Returns:
        The new Handoff's reference, the one a respawn out of quarantine must resume from.
    """
    base = await _last_handoff(warden, sub_bee)
    notes = (
        f"Quarantine checkpoint written by {warden._warden_id} for {sub_bee.worker_id} on task "
        f"{sub_bee.task_id}, ordered by {order.ordered_by.kind.value} {order.ordered_by.id}; its "
        f"memory from episode {order.lever.suspect_episode_id} on is suspect."
    )[:MAX_NOTES_CHARS]
    handoff = _carried(base, notes, warden, sub_bee) if base else _fresh(notes, warden, sub_bee)
    return await write_checkpoint(handoff, sub_bee.task_id, memory_context(warden))


def memory_context(warden: Warden) -> MemoryContext:
    """Return the memory context this Warden writes with: its store, identity and clock.

    Args:
        warden: The Warden whose collaborators to read.

    Returns:
        A MemoryContext over `WardenDeps.memory`, `.identity` and `.clock`.
    """
    deps = warden._deps
    return MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)


async def _last_handoff(warden: Warden, sub_bee: SubBee) -> Handoff | None:
    """Return the bee's own last Handoff when it can still be read, else None."""
    ref = sub_bee.last_handoff
    if ref is None:
        return None  # It never checkpointed this attempt: nothing to carry forward.
    allowance = HoneyClearance.from_wire(sub_bee.assignment.clearance)
    try:
        return await read_handoff(warden._deps.memory, ref, allowance)
    except (ClearanceError, TaintedMemoryError, HandoffNotFoundError):
        # Refused or gone: carrying it forward would launder a refused item into a fresh one.
        return None


def _carried(base: Handoff, notes: str, warden: Warden, sub_bee: SubBee) -> Handoff:
    """Rebuild the bee's last Handoff as the quarantine checkpoint, without any label it had."""
    fields = base.model_dump(exclude={"tainted", "notes", "written_by", "task_id"})
    return Handoff(**fields, notes=notes, written_by=warden._warden_id, task_id=sub_bee.task_id)


def _fresh(notes: str, warden: Warden, sub_bee: SubBee) -> Handoff:
    """Compose a checkpoint from the task's objective and the bee's last reported actions."""
    telemetry = sub_bee.last_telemetry
    actions = telemetry.last_actions if telemetry is not None else ()
    progress = "; ".join(actions) if actions else NO_PROGRESS
    return Handoff(
        goal=sub_bee.assignment.objective[:MAX_GOAL_CHARS],
        progress=progress[:MAX_PROGRESS_CHARS],
        decisions=(),
        tried_and_failed=(),
        constraints=(),
        open_threads=(),
        next_steps=(RESUME_STEP[:MAX_LIST_ITEM_CHARS],),
        do_not_redo=(),
        pinned_facts=(),
        notes=notes,
        clearance=HoneyClearance.from_wire(sub_bee.assignment.clearance),
        written_by=warden._warden_id,
        task_id=sub_bee.task_id,
    )
