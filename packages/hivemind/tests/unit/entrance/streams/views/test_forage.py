"""Test hivemind.entrance.streams.views.forage: the Forage ledger's deltas, as the Queen moves it.

Over a real listener and a real Queen: the grant she issues when she places a goal's task reaches
the view as the ``forage.granted`` event, the grant as her ledger holds it and the shared pool's
headroom after it, and a subscriber further behind than its backlog is closed with FELL_BEHIND.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/views/forage.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from typing import Any

from builders.entrance.serving import ProgramGrant, RigOptions, serving
from builders.entrance.views import burst, close_code, next_frame, open_view
from builders.human import single_task_plan
from builders.queen import plan_responder

from hivemind.cell import HoneyClearance
from hivemind.entrance.streams import CloseReason
from hivemind.llm import FakeLLMProvider

_OBSERVE = ProgramGrant(capabilities=("observe",))  # Ledger figures carry nothing personal.
_PATH = "/v1/forage/stream"


async def test_a_grant_the_queen_issues_arrives_with_the_headroom_it_leaves() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        socket = await open_view(rig, client, session, _PATH)
        try:
            goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
            issued = await rig.warden_end.wait_for_grant()
            frame: dict[str, Any] = {}
            while frame.get("event", {}).get("kind") != "forage.granted":
                frame = await next_frame(socket)
        finally:
            await socket.close()
        headroom = rig.deps.ledger.headroom()

    assert frame["type"] == "forage"
    assert frame["event"]["subject_id"] == issued.grant_id
    grant = frame["grant"]
    assert (grant["id"], grant["task_id"], grant["holder"]) == (
        issued.grant_id,
        goal_id,
        issued.holder,
    )
    assert grant["max_sub_bees"] == issued.max_sub_bees
    assert frame["headroom"] == {
        "sub_bees": headroom.sub_bees,
        "shared_seats": headroom.shared_seats,
    }


async def test_a_subscriber_further_behind_than_its_backlog_is_closed_as_fell_behind() -> None:
    async with serving(RigOptions(stream_backlog=2)) as rig:
        client, session = await rig.program(_OBSERVE)
        socket = await open_view(rig, client, session, _PATH)

        await burst(rig, "forage.capacity_reported", "cell_0000000000000000000000000A", 3)
        code = await close_code(socket)

    assert code == CloseReason.FELL_BEHIND.code
