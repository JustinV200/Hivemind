"""Test hivemind.entrance.streams.views.trail: every new trail event, filtered, as it is written.

Over a real listener and a real Queen planning a real goal: a family filter sends that family's
events as they land and nothing else, a task's title never rides along in a payload, and a
subscriber that falls further behind than its backlog is closed with FELL_BEHIND instead of
slowing the feed for everyone else.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/views/trail.py (codingrules section 3).

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

_OBSERVE = ProgramGrant(capabilities=("observe",))  # The trail needs nothing more.


async def _until_kind(socket: ClientConnection, kind: str) -> list[dict[str, Any]]:
    """Read frames until one carries ``kind``; every event read, in order."""
    events: list[dict[str, Any]] = []
    while not events or events[-1]["kind"] != kind:
        events.append((await next_frame(socket))["event"])
    return events


async def test_a_family_filter_sends_that_familys_events_as_a_goal_is_planned() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        socket = await open_view(rig, client, session, "/v1/trail/stream?family=task")
        try:
            goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
            events = await _until_kind(socket, "task.assigned")
        finally:
            await socket.close()

    assert events[0]["kind"] == "task.submitted"
    assert {event["family"] for event in events} == {"task"}
    assert {event["subject_id"] for event in events} == {goal_id}
    # The planner wrote the title from the goal: C2, read through the task's brief only.
    assert "title" not in events[0]["payload"]
    assert events[0]["payload"]["goal_id"] == goal_id


async def test_a_kind_filter_sends_only_that_kind() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        target = "/v1/trail/stream?kind=guard.entrance_invited"
        socket = await open_view(rig, client, session, target)
        console, console_session = await rig.console_session()
        try:
            await console.call(console_session, "POST", "/v1/entrance/invites", {"label": "tv"})
            frame = await next_frame(socket)
        finally:
            await socket.close()

    assert frame["type"] == "trail"
    assert frame["event"]["kind"] == "guard.entrance_invited"


async def test_a_subscriber_further_behind_than_its_backlog_is_closed_as_fell_behind() -> None:
    async with serving(RigOptions(stream_backlog=2)) as rig:
        client, session = await rig.program(_OBSERVE)
        socket = await open_view(rig, client, session, "/v1/trail/stream?family=cell")

        await burst(rig, "cell.ready", "cell_0000000000000000000000000A", 3)
        code = await close_code(socket)
        # The feed is not held up: the next view still hears the next event.
        after = await open_view(rig, client, session, "/v1/trail/stream?family=cell")
        await burst(rig, "cell.ready", "cell_0000000000000000000000000A", 1)
        frame = await next_frame(after)
        await after.close()

    assert code == CloseReason.FELL_BEHIND.code
    assert frame["event"]["kind"] == "cell.ready"
