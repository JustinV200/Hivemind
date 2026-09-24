"""Test hivemind.entrance.streams.views.cells: every Cell once, then each Cell whose status changed.

Over a real listener and a real Queen: the view first sends the Cell her attached Warden runs on
(tier, access level, Warden, no work), then, as she places a goal's task there, the same Cell
again with the task among its current tasks; a subscriber further behind than its backlog is
closed with FELL_BEHIND.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/views/cells.py (codingrules section 3).

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

_OBSERVE = ProgramGrant(capabilities=("observe",))  # A Cell's status carries nothing personal.
_PATH = "/v1/cells/stream"


async def test_every_cell_is_sent_first_then_again_as_work_is_placed_on_it() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        link = rig.queen.wardens[0]
        socket = await open_view(rig, client, session, _PATH)
        try:
            first = (await next_frame(socket))["cell"]
            goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
            changed: dict[str, Any] = first
            while goal_id not in changed["current_tasks"]:
                changed = (await next_frame(socket))["cell"]
        finally:
            await socket.close()

    cell = link.cell
    assert (first["id"], first["kind"], first["warden_id"]) == (cell.id, "REAL", link.warden_id)
    assert (first["comb_shield"], first["access_level"]) == (
        cell.comb_shield.value,
        cell.access_level.value,
    )
    assert first["current_tasks"] == [] and first["mask_state"] is None
    assert changed["id"] == cell.id


async def test_a_subscriber_further_behind_than_its_backlog_is_closed_as_fell_behind() -> None:
    async with serving(RigOptions(stream_backlog=2)) as rig:
        client, session = await rig.program(_OBSERVE)
        socket = await open_view(rig, client, session, _PATH)

        await burst(rig, "cell.ready", rig.queen.wardens[0].cell.id, 3)
        code = await close_code(socket)

    assert code == CloseReason.FELL_BEHIND.code
