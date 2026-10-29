"""Hold a placed task that would watch the owner until the owner approves it at the Hive Stand.

ADR-0069: once placement has chosen a Cell, the dispatcher asks `hold_for_owner` before it sizes
a grant or moves the task. A task whose Worker would be offered the operator's running screen
(`exoskeleton:real_display`: a desktop need, on a Cell whose operator opted its display in and
whose access level lets a bee drive it) or audio (`exoskeleton:audio`: an audio need where the
level allows it) files one watch request for that task on that Cell and stays PENDING, sent
nothing, until the owner answers from the Hive Stand. An APPROVED request lets it through (the
assignment then carries the approved scopes, `TaskAssign.owner_approved`); a DENIED or EXPIRED one
cancels the task with the reason, the chamber's clean PENDING -> CANCELLED edge. A task that asks
for neither scope, the virtual display included, is never held. Nothing here approves anything:
only the owner's answer through the Queen's door does.

A retry or a resume skips placement, so it asks `hand_back_unapproved` instead: a task whose
approval this Queen does not hold for its Cell (she restarted, or a Supersedure handed her the
Hive, and the book is in memory) goes back to PENDING (`chamber.unassign`, a new attempt), its
grants released and a resume's Handoff noted, so the next pass places it afresh and the owner is
asked again rather than the task losing the screen or audio without a word.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    peripherals sub-package. Called by `hivemind.queen.dispatcher.ready.dispatch` directly (the
    face never imports it: it needs `QueenDeps`, which imports the face). Calls into
    `hivemind.brood_chamber` (Task), `hivemind.cell` (Cell, TaskNeeds), `hivemind.guard`
    (ceiling_for), `hivemind.pheromone`, `hivemind.queen.trail` and waggle only.

Key invariants:
    - A task needing an owner-approved scope is never assigned while its request is not APPROVED.
    - A request is filed once per task, Cell and scopes, and said on the trail once, when filed.
    - A retry or resume is never sent with fewer scopes than its task needs there: unapproved, it
      is placed afresh instead.

See Also:
    - hivemind.queen.peripherals.book for the requests and their answers.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, TaskNeeds
from hivemind.guard import Capability, ceiling_for
from hivemind.pheromone import MAX_PAYLOAD_STRING_CHARS
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.peripherals.book import WATCH_REQUEST_TTL
from hivemind.queen.peripherals.model import WatchRequest, WatchStatus, new_watch_id
from hivemind.queen.trail import record_event
from waggle.messages import HandoffRef

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink

_REAL_DISPLAY = Capability.parse("exoskeleton:real_display")
_AUDIO = Capability.parse("exoskeleton:audio")
# WHY a stand-in: `ceiling_for` reads a scratch root only for its `fs:write` scope, and no lease
# exists yet; no Exoskeleton scope depends on it (as in `hivemind.queen.placement.rules`).
_NO_LEASE_SCRATCH_ROOT = PurePosixPath("/")
# Why an unapproved retry or resume is placed afresh: plain words, no content (codingrules 12).
ASK_AGAIN_REASON = "The owner's approval for its screen or audio is not on record; asking again."

__all__ = ["ASK_AGAIN_REASON", "hand_back_unapproved", "hold_for_owner", "watching_scopes"]


def watching_scopes(needs: TaskNeeds, cell: Cell) -> tuple[str, ...]:
    """Return the owner-approved scopes a task with `needs` would be offered on `cell`, sorted.

    Mirrors what the Cell's Warden could hand its Worker: the running display only for a desktop
    need on a Cell that has one, its operator opted in and its level allows; audio for an audio
    need where the level allows it. Empty for a terminal, browser-only or virtual-display task.
    """
    if not needs.exoskeleton or needs.browser_only:
        return ()
    capabilities = cell.capabilities
    ceiling = ceiling_for(
        cell.access_level,
        _NO_LEASE_SCRATCH_ROOT,
        real_display=capabilities.real_display_allowed,
        processes_contained=capabilities.processes_contained,
    )
    scopes: list[str] = []
    if capabilities.has_display and ceiling.allows(_REAL_DISPLAY):
        scopes.append(str(_REAL_DISPLAY))
    if needs.audio and ceiling.allows(_AUDIO):
        scopes.append(str(_AUDIO))
    return tuple(sorted(scopes))


async def hold_for_owner(deps: QueenDeps, task: Task, link: WardenLink) -> bool:
    """Return whether `task` must wait (or was just refused) rather than be assigned on `link`.

    Args:
        deps: The Queen's collaborators; `dispatch.watch` holds the requests.
        task: The PENDING task placement put on `link`'s Cell.
        link: The Warden link of that Cell.

    Returns:
        False when the task needs no owner-approved scope there, or the owner approved them all;
        True when it waits for the owner, or was cancelled because the owner said no or never
        answered.
    """
    scopes = watching_scopes(task.spec.needs, link.cell)
    if not scopes:
        return False
    book, now = deps.dispatch.watch, deps.clock.now()
    book.expire(now)
    request = book.find(task.id, link.cell.id, scopes)
    if request is None:
        await _file(deps, task, link, scopes)
        return True
    if request.status is WatchStatus.APPROVED:
        return False
    if request.status is WatchStatus.PENDING:
        return True
    outcome = request.status.value.lower()
    reason = f"The owner did not approve {', '.join(scopes)} on {link.cell.name} ({outcome})."
    await deps.chamber.cancel(task.id, reason[:MAX_PAYLOAD_STRING_CHARS])
    return True


async def hand_back_unapproved(
    deps: QueenDeps, task: Task, link: WardenLink, resume_from: HandoffRef | None
) -> bool:
    """Return whether `task` was given back to PENDING rather than retried or resumed on `link`.

    Args:
        deps: The Queen's collaborators; `dispatch.watch` holds the approvals.
        task: The RUNNING (retry) or PAUSED (resume) task placed on `link`'s Cell.
        link: The Warden link of that Cell.
        resume_from: The Handoff a resume starts from, kept for the fresh placement; None for a
            retry.

    Returns:
        False when the task needs no owner-approved scope there, or the owner approved them all
        in this Queen's life; True once it is PENDING again (or moved on meanwhile) and nothing
        was sent.
    """
    scopes = watching_scopes(task.spec.needs, link.cell)
    approved = deps.dispatch.watch.approved_scopes(task.id, link.cell.id)
    if set(scopes) <= set(approved):
        return False
    # Under the dispatch lock, like every write that moves a task in or out of PENDING.
    async with deps.dispatch.lock:
        await deps.chamber.unassign(task.id, ASK_AGAIN_REASON)
        await forage_grants.release_finished(deps.ledger, deps, {task.id}, reason=ASK_AGAIN_REASON)
    if resume_from is not None:
        deps.dispatch.resumes.note(task.id, resume_from)
    deps.wake.set()  # Her next pass places it and files the request at once.
    return True


async def _file(deps: QueenDeps, task: Task, link: WardenLink, scopes: tuple[str, ...]) -> None:
    """File a PENDING request for `task` on `link`'s Cell and say so on the trail, once."""
    now = deps.clock.now()
    request = WatchRequest(
        id=new_watch_id(deps.clock),
        task_id=task.id,
        title=task.spec.title,
        cell_id=link.cell.id,
        cell_name=link.cell.name,
        warden_id=link.warden_id,
        role=task.spec.role,
        scopes=scopes,
        requested_at=now,
        expires_at=now + WATCH_REQUEST_TTL,
    )
    deps.dispatch.watch.file(request)
    # Ids and scope names only (codingrules section 12): the title is shown, never recorded.
    await record_event(
        deps,
        "queen.decided",
        task.id,
        reason="owner_approval_pending",
        request_id=request.id,
        cell_id=link.cell.id,
        scopes=list(scopes),
    )
