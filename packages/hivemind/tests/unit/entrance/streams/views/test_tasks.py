"""Test hivemind.entrance.streams.views.tasks: one principal's task-graph deltas, without words.

Over a real listener and a real Queen placing a real goal: the Queen's graph shows every task as
it changes, a Warden's graph shows the task placed on it and then hears it leave when the goal is
cancelled, no frame ever carries a task's words, and a subscriber further behind than its backlog
is closed with FELL_BEHIND.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/views/tasks.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from typing import Any

from builders.entrance.serving import ProgramGrant, RigOptions, serving
from builders.entrance.views import burst, close_code, next_frame, open_view
from builders.human import single_task_plan
from builders.queen import plan_responder
from websockets.asyncio.client import ClientConnection

from hivemind.cell import HoneyClearance
from hivemind.entrance.streams import CloseReason
from hivemind.llm import FakeLLMProvider

_OBSERVE = ProgramGrant(capabilities=("observe",))  # A graph without words is a read-only view.
_WORDS = {"title", "objective", "acceptance", "last_summary"}  # A task's words: C2, never here.
_PLACED = {"ASSIGNED", "RUNNING"}  # A batch sends a task as it stands: placed, maybe started.


async def _until_status(socket: ClientConnection, statuses: set[str]) -> list[dict[str, Any]]:
    """Read frames until one shows a task in one of ``statuses``; every frame read, in order."""
    frames: list[dict[str, Any]] = []
    while not frames or frames[-1]["task"]["status"] not in statuses:
        frames.append(await next_frame(socket))
    return frames


async def test_a_wardens_graph_shows_its_task_placed_and_then_leaving() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        warden_id = rig.queen.wardens[0].warden_id
        target = f"/v1/tasks/stream?principal={warden_id}"
        socket = await open_view(rig, client, session, target)
        try:
            goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
            placed = await _until_status(socket, _PLACED)
            await rig.warden_end.wait_for_assignment()
            stopped = await rig.queen.cancel_goal(goal_id, "operator_cancelled")
            left = await _until_status(socket, {"CANCELLED"})
        finally:
            await socket.close()

    assert stopped
    frames = placed + left
    assert {frame["principal"] for frame in frames} == {warden_id}
    assert {frame["task"]["id"] for frame in frames} == {goal_id}
    assert placed[-1]["task"]["warden_id"] == warden_id
    assert left[-1]["task"]["outcome"]["status"] == "CANCELLED"
    assert all(not _WORDS & set(frame["task"]) for frame in frames)


async def test_the_queens_graph_shows_every_task_as_it_changes() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        socket = await open_view(rig, client, session, "/v1/tasks/stream")
        try:
            goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
            frames = await _until_status(socket, _PLACED)
        finally:
            await socket.close()

    assert {frame["principal"] for frame in frames} == {"queen"}
    assert frames[-1]["task"]["id"] == goal_id
    assert frames[-1]["task"]["goal_id"] == goal_id


async def test_a_subscriber_further_behind_than_its_backlog_is_closed_as_fell_behind() -> None:
    async with serving(RigOptions(stream_backlog=2)) as rig:
        client, session = await rig.program(_OBSERVE)
        socket = await open_view(rig, client, session, "/v1/tasks/stream")

        await burst(rig, "task.progressed", "task_0000000000000000000000000A", 3)
        code = await close_code(socket)

    assert code == CloseReason.FELL_BEHIND.code
