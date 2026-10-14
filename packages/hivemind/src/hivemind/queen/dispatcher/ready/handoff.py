"""Define resume_plan and fit_in_frame: the Handoff a resuming task carries, inline when it must.

A task resumes from a Handoff (the structured document a bee writes before its context is reset)
named by a `HandoffRef`, the trail event that recorded it. Where the document lives depends on who
wrote it. A Warden that shares the Queen's memory store (the Hive Stand's, a Level 0 device's) and
its bees wrote it where the Queen reads it, so it needs the reference alone. One that does not (a
Virtual Cell's in-Cell Warden, a colonized device's, any Warden on another node than the Queen's,
told apart by the node its link proved) keeps its own bees' Handoffs in its own store, which is
where its resume reads them. The Queen holds a copy only when one reached her, as it does when a
Warden migrates onto a device: the Handoff is written on the Hive Stand before the Warden moves
(ADR-0052). So for a remote Warden: when she holds the document she carries it in
`TaskAssign.handoff` (Waggle 1.13), because the Warden cannot read her store; when she does not,
she sends the reference alone and the Warden reads its own store. A Warden whose store lacks it too
has its bee start fresh (`hivemind.workers.runtime.attempt.begin_attempt`), so a task resumes fresh
only when nobody can supply the document.

Three things stop a Handoff travelling, and each one resumes the task fresh instead, with the
reason recorded as `queen.decided` (a stable word, never the document): the Queen's own read
refuses it (it is tainted, or above the task's clearance, which no bee may resume from, so she
sends no reference to it either), or the assignment would not fit one frame with the document in
it. A fresh start loses the progress; a Handoff nobody may read, or cannot receive, would lose the
task. Her store merely not holding a Handoff is none of these: it says nothing about the
Warden's, and used to resume every task on a Virtual Cell or a colonized device from nothing.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher.ready`
    sub-package. Called by `.dispatch` (a resumed or re-placed task) and `.assign` (the frame
    check). Calls into `hivemind.memory` (read_handoff and its errors), `hivemind.queen.deps`,
    `hivemind.queen.trail` (record_event) and waggle (the codec's limit, the envelope, the
    messages) only.

Key invariants:
    - A tainted Handoff and one above the task's clearance never travel, as a document or as a
      reference; the task resumes fresh and says why on the trail.
    - A Warden that shares the Queen's store is sent the reference alone, exactly as before.
    - A remote Warden is sent the document with the reference when the Queen holds it, and the
      reference alone when she does not: her store lacking a Handoff says nothing about the
      Warden's own, which is where a Cell's or a device's bees wrote it.
    - An assignment whose frame, signed, would exceed `MAX_FRAME_BYTES` is sent without the
      document and without the reference.
    - Nothing here logs or records the document, its text or its size; the reason is a word.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for why a migration carries it inline.
    - waggle.messages.task.assignment for TaskAssign.handoff and the validators on it.
    - hivemind.memory.checkpoint for read_handoff, the one reader.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.brood_chamber import Task
from hivemind.memory import ClearanceError, HandoffNotFoundError, TaintedMemoryError, read_handoff
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.trail import record_event
from waggle.codec import MAX_FRAME_BYTES, SIGNATURE_OVERHEAD_BYTES, Codec
from waggle.envelope import wrap
from waggle.errors import FrameTooLargeError
from waggle.messages import HandoffRef
from waggle.messages.task import HandoffDocument, TaskAssign

REASON_TAINTED = (
    "handoff_tainted"  # The Queen's read refuses a tainted Handoff, whatever its label.
)
REASON_ABOVE_CLEARANCE = "handoff_above_clearance"  # Above the label the task's bee may read.
REASON_TOO_LARGE = "handoff_too_large"  # The assignment with the document would not fit a frame.

# The largest unsigned frame the Queen builds, so that once signed it still fits the codec's limit.
UNSIGNED_FRAME_BYTES = MAX_FRAME_BYTES - SIGNATURE_OVERHEAD_BYTES

__all__ = [
    "REASON_ABOVE_CLEARANCE",
    "REASON_TAINTED",
    "REASON_TOO_LARGE",
    "UNSIGNED_FRAME_BYTES",
    "ResumePlan",
    "fit_in_frame",
    "resume_plan",
]


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """What a resuming task's assignment carries about its Handoff.

    Attributes:
        resume_from: The reference to resume from, or None for a fresh start.
        handoff: The document itself, when the target Warden cannot read the Queen's store; set
            only with `resume_from`.
    """

    resume_from: HandoffRef | None = None
    handoff: HandoffDocument | None = None


async def resume_plan(
    deps: QueenDeps, link: WardenLink, task: Task, ref: HandoffRef | None
) -> ResumePlan:
    """Decide what `task` carries to resume from `ref` on `link`'s Warden.

    Args:
        deps: The Queen's collaborators; her memory store is read for a remote Warden.
        link: The Warden the task goes to.
        task: The task being resumed; its clearance is the most the Handoff may be labelled.
        ref: The Handoff to resume from, or None for a fresh start.

    Returns:
        The reference alone for a Warden that shares her store, and for a remote one when she
        holds no copy (it reads its own store); the reference and the document for a remote one
        when she does; nothing at all (a fresh start, with the reason on the trail) when the
        Handoff may not be used.
    """
    if ref is None:
        return ResumePlan()
    if not _is_remote(deps, link):
        return ResumePlan(resume_from=ref)  # It reads the same store the Queen does.
    try:
        handoff = await read_handoff(deps.memory, ref, task.spec.clearance)
    except HandoffNotFoundError:
        # No copy here, which says nothing about the Warden's store: its bees wrote their
        # Handoffs there (a Virtual Cell's, a colonized device's), and a resume reads them there.
        # The reference travels alone for it to resolve; dropping it resumed every such task from
        # nothing, and gave the Cell's own taint gate no reference to refuse a tainted one by.
        return ResumePlan(resume_from=ref)
    except TaintedMemoryError:
        return await _fresh(deps, task, REASON_TAINTED)
    except ClearanceError:
        return await _fresh(deps, task, REASON_ABOVE_CLEARANCE)
    # The taint label is the Queen's own bookkeeping and never travels: a CLEARED one is clean.
    document = HandoffDocument.model_validate(handoff.model_dump(mode="json", exclude={"tainted"}))
    return ResumePlan(resume_from=ref, handoff=document)


async def fit_in_frame(
    deps: QueenDeps,
    link: WardenLink,
    task: Task,
    assign: TaskAssign,
    limit: int = UNSIGNED_FRAME_BYTES,
) -> TaskAssign:
    """Return `assign`, or the same assignment as a fresh start if it would not fit a frame.

    Args:
        deps: The Queen's collaborators; her clock stamps the trial frame.
        link: The Warden it goes to, whose hop the frame is built with.
        task: The task, named on the trail if its Handoff is dropped.
        assign: The assignment about to be sent.
        limit: The largest unsigned frame that still fits once signed (a test narrows it).

    Returns:
        `assign` unchanged when it carries no document or fits; otherwise without the document and
        the reference it came with.
    """
    if assign.handoff is None:
        return assign
    # A codec with no signer and the limit less a signature: if this encodes, the signed frame
    # the transport builds fits too. Pure CPU, once, and only when a document travels.
    trial = Codec(max_frame_bytes=limit)
    try:
        trial.encode(wrap(assign, link.hop, clock=deps.clock))
    except FrameTooLargeError:
        await record_event(
            deps, "queen.decided", task.id, reason="resume_fresh", detail=REASON_TOO_LARGE
        )
        return assign.model_copy(update={"handoff": None, "resume_from": None})
    return assign


def _is_remote(deps: QueenDeps, link: WardenLink) -> bool:
    """Whether `link`'s Warden signs as a node other than the Queen's, so lacks her store."""
    return link.node_id is not None and link.node_id != deps.identity.node_id


async def _fresh(deps: QueenDeps, task: Task, reason: str) -> ResumePlan:
    """Resume `task` from nothing, saying why on the trail."""
    await record_event(deps, "queen.decided", task.id, reason="resume_fresh", detail=reason)
    return ResumePlan()
