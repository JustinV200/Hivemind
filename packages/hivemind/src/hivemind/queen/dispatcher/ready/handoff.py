"""Define resume_plan and fit_in_frame: the Handoff a resuming task carries, inline when it must.

A task resumes from a Handoff (the structured document a bee writes before its context is reset)
named by a `HandoffRef`, the trail event that recorded it. A Warden that shares the Queen's memory
store finds the document there. One that does not cannot: a Warden that migrated onto a colonized
device (ADR-0052), a Virtual Cell's, or any Warden on another node than the one the Handoff was
written on, so the Queen reads the document from her own store and carries it in
`TaskAssign.handoff` (Waggle 1.13). Whether the target shares her store is read off its link: a
Warden whose link proved a node other than the Queen's is remote, and a migration resume always
lands on one, so it always carries the document.

Three things stop a Handoff travelling, and each one resumes the task fresh instead, with the
reason recorded as `queen.decided` (a stable word, never the document): the Queen's own read
refuses it (it is tainted, or above the task's clearance, which no bee may resume from), her store
does not hold it (so the remote Warden cannot have it either), or the assignment would not fit one
frame with the document in it. A fresh start loses the progress; a Handoff nobody may read, or
cannot receive, would lose the task.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher.ready`
    sub-package. Called by `.dispatch` (a resumed or re-placed task) and `.assign` (the frame
    check). Calls into `hivemind.memory` (read_handoff and its errors), `hivemind.queen.deps`,
    `hivemind.queen.trail` (record_event) and waggle (the codec's limit, the envelope, the
    messages) only.

Key invariants:
    - A tainted Handoff, one above the task's clearance and one the Queen cannot read never travel;
      the task resumes fresh and says why on the trail.
    - A Warden that shares the Queen's store is sent the reference alone, exactly as before.
    - An assignment whose frame, signed, would exceed `MAX_FRAME_BYTES` is sent without the
      document and without the reference: the receiver is never handed a reference it cannot
      resolve.
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
REASON_NOT_HELD = "handoff_not_held"  # Not in her store, so a remote Warden lacks it too.
REASON_TOO_LARGE = "handoff_too_large"  # The assignment with the document would not fit a frame.

# The largest unsigned frame the Queen builds, so that once signed it still fits the codec's limit.
UNSIGNED_FRAME_BYTES = MAX_FRAME_BYTES - SIGNATURE_OVERHEAD_BYTES

__all__ = [
    "REASON_ABOVE_CLEARANCE",
    "REASON_NOT_HELD",
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
        The reference alone for a Warden that shares her store; the reference and the document
        for a remote one; nothing at all (a fresh start, with the reason on the trail) when the
        Handoff may not or cannot travel.
    """
    if ref is None:
        return ResumePlan()
    if not _is_remote(deps, link):
        return ResumePlan(resume_from=ref)  # It reads the same store the Queen does.
    try:
        handoff = await read_handoff(deps.memory, ref, task.spec.clearance)
    except TaintedMemoryError:
        return await _fresh(deps, task, REASON_TAINTED)
    except ClearanceError:
        return await _fresh(deps, task, REASON_ABOVE_CLEARANCE)
    except HandoffNotFoundError:
        return await _fresh(deps, task, REASON_NOT_HELD)
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
