"""Tests for hivemind.queen.inbox.claims: a Warden speaks only for its own Cell's tasks.

An item a Warden sent about a live task placed with another Warden is refused as `another_cell`,
one about a task placed with no Warden as `unplaced`, and either is recorded as
`guard.task_refused` about the sending Cell, naming its Warden and the message's kind but never
the task, once per Warden, kind and reason. A Warden's item about its own task, the human's item
about any task, and any item about a task that already ended all pass.

Fits into the Hive:
    Mirrors src/hivemind/queen/inbox/claims.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.queen.test_queen_claims for the same refusals inside the Queen's own tick.
"""

from __future__ import annotations

from builders.cells import make_cell
from builders.isolation import place_running
from builders.queen import make_queen_deps, make_warden_link
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskOutcome, TaskStatus
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.inbox import HUMAN_PRINCIPAL, TASK_REFUSED_KIND, TaskClaims, to_inbox_item
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import Hop, wrap
from waggle.ids import TaskId, WardenId, new_node_id, new_warden_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskOutcome as WireTaskOutcome
from waggle.messages.task import TaskResult


def _result(task_id: TaskId, warden_id: WardenId) -> TaskResult:
    """A Warden's verified TaskResult(SUCCEEDED) for `task_id`."""
    return TaskResult(
        task_id=task_id,
        attempt=1,
        outcome=WireTaskOutcome.SUCCEEDED,
        summary="Done.",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=warden_id,
        handoff=None,
        spend=0.0,
        reason="Checks passed.",
    )


def _item(
    deps: QueenDeps, principal: str, task_id: TaskId, checked_by: WardenId | None = None
) -> InboxItem:
    """The item a TaskResult about `task_id` becomes once drained as `principal`'s."""
    checker = checked_by if checked_by is not None else WardenId(principal)
    hop = Hop(sender=checker, recipient=deps.identity.hive_id, node_id=deps.identity.node_id)
    envelope = wrap(_result(task_id, checker), hop, clock=deps.clock)
    return to_inbox_item(envelope, principal)


def _second_warden(deps: QueenDeps) -> WardenLink:
    """Another attached Warden, on a Cell of its own."""
    clock = deps.clock
    cell = make_cell(clock=clock)
    link, _end = make_warden_link(
        deps.identity.hive_id, new_warden_id(clock), new_node_id(clock), cell, clock
    )
    return link


async def _refused(deps: QueenDeps) -> list[PheromoneEvent]:
    """Every `guard.task_refused` on the Queen's trail."""
    return list(await deps.trail.query(TrailQuery(kind=TASK_REFUSED_KIND)))


async def test_a_warden_is_refused_another_cells_task_and_the_refusal_names_only_itself() -> None:
    deps, owner, owner_end = make_queen_deps()
    other = _second_warden(deps)
    task = await place_running(deps, owner)
    claims = TaskClaims()

    refused = await claims.refuse(
        deps,
        {owner.warden_id: owner, other.warden_id: other},
        _item(deps, other.warden_id, task.id),
        task,
    )

    assert refused
    [event] = await _refused(deps)
    assert event.subject_id == other.cell.id
    assert event.payload == {
        "cell_id": other.cell.id,
        "warden_id": other.warden_id,
        "message": "task.result",
        "reason": "another_cell",
    }
    await owner_end.close()


async def test_a_task_placed_nowhere_is_refused_as_unplaced_once_per_warden_and_kind() -> None:
    deps, link, warden_end = make_queen_deps()
    [pending] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    claims, wardens = TaskClaims(), {link.warden_id: link}
    item = _item(deps, link.warden_id, pending.id)

    first = await claims.refuse(deps, wardens, item, pending)
    again = await claims.refuse(deps, wardens, item, pending)

    assert first and again  # Refused every time...
    [event] = await _refused(deps)  # ...and recorded once.
    assert event.payload["reason"] == "unplaced"
    await warden_end.close()


async def test_its_own_task_the_humans_word_and_a_finished_task_all_pass() -> None:
    deps, link, warden_end = make_queen_deps()
    other = _second_warden(deps)
    task = await place_running(deps, link)
    wardens, claims = {link.warden_id: link, other.warden_id: other}, TaskClaims()
    finished = await _finished(deps, other)

    own = await claims.refuse(deps, wardens, _item(deps, link.warden_id, task.id), task)
    human_item = _item(deps, HUMAN_PRINCIPAL, task.id, checked_by=link.warden_id)
    human = await claims.refuse(deps, wardens, human_item, task)
    stale = await claims.refuse(deps, wardens, _item(deps, link.warden_id, finished.id), finished)

    assert not (own or human or stale)
    assert await _refused(deps) == []
    await warden_end.close()


async def _finished(deps: QueenDeps, link: WardenLink) -> Task:
    """A task `link`'s Warden ran and finished: its placement is gone with its terminal edge."""
    task = await place_running(deps, link)
    outcome = TaskOutcome(status=TaskStatus.SUCCEEDED, summary="Done.", verified_by=link.warden_id)
    return await deps.chamber.complete(task.id, outcome)
