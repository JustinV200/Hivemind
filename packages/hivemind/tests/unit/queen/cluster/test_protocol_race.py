"""Tests for hivemind.queen.cluster.protocol: a task that moves on while a pause waits for it.

`pause_on_warden` waits, in scheduling turns and (for a move) in real time, for a bee to write its
Handoff, and the Queen's tick runs beside that wait. A bee that finishes its command meanwhile, or
a human who cancels, moves the task on; the pause must then leave it exactly as it is, as it does
for a task that was not RUNNING before the wait, and never raise the chamber's conflict into the
swarm gate's flows and passes that call it.

Fits into the Hive:
    Mirrors src/hivemind/queen/cluster/protocol.py (codingrules section 3; split by feature per
    14.2, beside test_protocol.py): a Queen with one task RUNNING on an attached Warden, the
    Warden's end read for what it was sent.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cluster.protocol for the module under test.
    - tests.unit.queen.cluster.test_protocol for the pause's ordering and patience.
"""

from __future__ import annotations

import pytest
from builders.isolation import place_running
from builders.queen import make_queen_deps

from hivemind.brood_chamber import TaskOutcome, TaskStatus
from hivemind.queen.cluster import protocol
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.queen import Queen
from waggle.ids import TaskId


async def _complete(deps: QueenDeps, link: WardenLink, task_id: TaskId) -> None:
    """Land the bee's result, as the Queen's tick does when the Warden reports it."""
    await deps.chamber.complete(
        task_id,
        TaskOutcome(status=TaskStatus.SUCCEEDED, summary="done", verified_by=link.warden_id),
    )


async def test_a_task_that_finishes_while_the_pause_waits_for_its_handoff_is_not_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    task = await place_running(deps, link)
    real = protocol._send_handoff_intervene
    pauses_sent: list[TaskId] = []

    async def finish_then_send(
        deps_: QueenDeps, link_: WardenLink, task_id: TaskId, reason: str
    ) -> None:
        await _complete(deps_, link_, task_id)  # The tick lands the result during the wait.
        await real(deps_, link_, task_id, reason)

    async def spy(_deps: QueenDeps, _link: WardenLink, task_id: TaskId, _reason: str) -> None:
        pauses_sent.append(task_id)

    monkeypatch.setattr(protocol, "_send_handoff_intervene", finish_then_send)
    monkeypatch.setattr(protocol, "_send_task_pause", spy)

    paused = await protocol.pause_on_warden(deps, queen.wardens, link.warden_id, "Moving.")

    assert paused == ()  # Nothing raised, and the task was not counted as paused.
    assert pauses_sent == []  # A bee that is gone is sent no pause.
    assert (await deps.chamber.get(task.id)).status is TaskStatus.SUCCEEDED
    await queen.stop()


async def test_a_task_that_finishes_just_before_the_chamber_pause_is_left_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps, link, _end = make_queen_deps()
    queen = Queen(deps)
    await queen.attach_warden(link)
    task = await place_running(deps, link)
    real = protocol._send_task_pause

    async def finish_then_send(
        deps_: QueenDeps, link_: WardenLink, task_id: TaskId, reason: str
    ) -> None:
        # The one await between the re-read and the chamber's pause: the result lands here.
        await _complete(deps_, link_, task_id)
        await real(deps_, link_, task_id, reason)

    monkeypatch.setattr(protocol, "_send_task_pause", finish_then_send)

    paused = await protocol.pause_on_warden(deps, queen.wardens, link.warden_id, "Moving.")

    assert paused == ()
    assert (await deps.chamber.get(task.id)).status is TaskStatus.SUCCEEDED
    await queen.stop()
