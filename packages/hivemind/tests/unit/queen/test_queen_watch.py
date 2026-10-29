"""Tests for ADR-0069 through a real Queen: a task that would watch the owner waits for their yes.

The planner asks for a desktop with audio on a Cell whose operator opted its running display in,
under the operator's own local path (no goal ceiling, the widest a planner can ever get). The
task must stay PENDING with nothing sent to the Warden until the owner answers; a yes reaches the
Warden as `TaskAssign.owner_approved`, a no or no answer at all cancels the task.

Fits into the Hive:
    Mirrors src/hivemind/queen/peripherals/ (codingrules section 3), end to end through the
    dispatcher, as test_queen_dispatch.py does for the plain path.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from builders.cells import make_capabilities, make_cell
from builders.queen import WardenEnd, make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import AccessLevel, HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready, redispatch, resume_paused
from hivemind.queen.peripherals import (
    MAX_WATCH_REQUESTS,
    WATCH_REQUEST_TTL,
    WatchStatus,
    new_watch_id,
)
from hivemind.queen.queen import Queen
from waggle.clock import FakeClock
from waggle.ids import DeviceId, new_device_id

WATCHING = ("exoskeleton:audio", "exoskeleton:real_display")


def _watching_plan(goal: str) -> dict[str, object]:
    """One Drone task needing a desktop with audio: the screen and the sound server."""
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Listen to the meeting",
                "objective": goal,
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/notes.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {"exoskeleton": True, "audio": True},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


async def _waiting_queen(clock: FakeClock) -> tuple[Queen, QueenDeps, WardenLink, WardenEnd]:
    """A Queen whose one task is held for the owner on a Cell lending its running display."""
    capabilities = make_capabilities(
        has_display=True, has_audio=True, can_start_display=True, real_display_allowed=True
    )
    cell = make_cell(access_level=AccessLevel.FULL, capabilities=capabilities, clock=clock)
    provider = FakeLLMProvider(responder=plan_responder(_watching_plan))
    deps, link, warden_end = make_queen_deps(clock, fake_provider=provider, cell=cell)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Take notes from the call.", clearance=HoneyClearance.C1)
    return queen, deps, link, warden_end


@pytest.mark.parametrize("answer", ["approve", "deny", "expire"])
async def test_a_task_that_would_watch_the_owner_waits_for_their_answer(answer: str) -> None:
    clock = FakeClock()
    queen, deps, link, warden_end = await _waiting_queen(clock)
    (task,) = await deps.chamber.list(TaskFilter())

    # Planned and placed, but held: nothing was sent, and one request names the task and Cell.
    (request,) = queen.pending_watch_requests()
    assert task.status is TaskStatus.PENDING
    assert (request.task_id, request.cell_id, request.scopes) == (task.id, link.cell.id, WATCHING)

    device: DeviceId = new_device_id(clock)
    if answer == "expire":
        clock.advance(WATCH_REQUEST_TTL.total_seconds())
    else:
        await queen.answer_watch_request(request.id, answer == "approve", device)
    await dispatch_ready(deps, [link])

    if answer == "approve":
        assignment = await warden_end.wait_for_assignment()
        assert assignment.owner_approved == WATCHING
    else:
        assert (await deps.chamber.get(task.id)).status is TaskStatus.CANCELLED
        assert warden_end.assignments == []
    await warden_end.close()


@pytest.mark.parametrize("path", ["retry", "resume"])
async def test_a_retry_or_resume_without_the_approval_on_record_asks_again(path: str) -> None:
    clock = FakeClock()
    queen, deps, link, warden_end = await _waiting_queen(clock)
    (request,) = queen.pending_watch_requests()
    await queen.answer_watch_request(request.id, True, new_device_id(clock))
    await dispatch_ready(deps, [link])
    await warden_end.wait_for_assignment()
    task = await deps.chamber.get(request.task_id)

    # A restarted (or superseding) Queen's book is empty: the old yes is not on record.
    deps.dispatch.watch.requests.clear()
    if path == "retry":
        await redispatch(deps, [link], task.id, task.attempt + 1)
    else:
        await deps.chamber.pause(task.id, "Clustering paused.")
        await resume_paused(deps, [link], task.id, None, "Clustering resumed.")

    # Not resent with the scopes stripped: placed afresh, and the owner asked again.
    assert (await deps.chamber.get(task.id)).status is TaskStatus.PENDING
    await dispatch_ready(deps, [link])
    (again,) = queen.pending_watch_requests()
    assert (again.task_id, again.scopes) == (task.id, WATCHING)
    assert len(warden_end.assignments) == 1
    await warden_end.close()


async def test_a_full_book_never_drops_a_live_tasks_approval() -> None:
    clock = FakeClock()
    queen, deps, link, warden_end = await _waiting_queen(clock)
    (request,) = queen.pending_watch_requests()
    await queen.answer_watch_request(request.id, True, new_device_id(clock))
    clock.advance(1)  # Every refusal filed below is newer than the approval.
    book = deps.dispatch.watch
    for _ in range(MAX_WATCH_REQUESTS + 1):
        filler = replace(request, id=new_watch_id(clock), status=WatchStatus.DENIED)
        book.file(filler)

    # Refusals went past the bound and were dropped; the approval a retry needs was kept.
    assert len(book.requests) == MAX_WATCH_REQUESTS
    assert book.approved_scopes(request.task_id, link.cell.id) == WATCHING
    await warden_end.close()
