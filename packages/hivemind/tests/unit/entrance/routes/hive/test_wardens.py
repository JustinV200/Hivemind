"""Test hivemind.entrance.routes.hive.wardens: each Warden's state, pulse, sub-bees and grants.

Over real listeners and a real, running Queen: before its first Heartbeat a Warden is listed with
its Cell and no state; once the Queen has received a Heartbeat listing two sub-bees and placed a
goal's task on it, the read shows the state it reported, the Queen's record of its pulse, two
sub-bees and the live grant it holds. A submit-only device is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/hive/wardens.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio

from builders.entrance.serving import ProgramGrant, RigOptions, serving
from builders.human import single_task_plan
from builders.queen import plan_responder
from builders.supervision import make_telemetry

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from waggle.clock import Clock
from waggle.ids import new_worker_id
from waggle.messages.supervision import ChildTelemetry, Heartbeat, WardenState, WorkerState

_OBSERVE = ProgramGrant(capabilities=("observe",))


def _heartbeat(clock: Clock, sub_bees: int) -> Heartbeat:
    """An ACTIVE Warden's Heartbeat listing ``sub_bees`` running sub-bees."""
    children = tuple(
        ChildTelemetry(
            worker_id=new_worker_id(clock),
            task_id=None,
            state=WorkerState.RUNNING,
            telemetry=make_telemetry(),
        )
        for _ in range(sub_bees)
    )
    return Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=children,
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


async def test_a_warden_shows_its_reported_state_pulse_sub_bees_and_grants() -> None:
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        client, session = await rig.program(_OBSERVE)
        link = rig.queen.wardens[0]
        before = await client.call(session, "GET", "/v1/wardens")
        running = asyncio.ensure_future(rig.queen.run())
        try:
            await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
            await rig.warden_end.wait_for_grant()
            await rig.warden_end.send(_heartbeat(rig.clock, sub_bees=2))
            await rig.until(lambda: link.warden_id in rig.telemetry.latest())
            after = await client.call(session, "GET", "/v1/wardens")
        finally:
            await rig.queen.stop()
            await running

    [fresh] = before.json()["wardens"]
    assert (fresh["id"], fresh["cell_id"]) == (link.warden_id, link.cell.id)
    assert (fresh["state"], fresh["sub_bees"], fresh["last_heartbeat_at"]) == (None, 0, None)
    [warden] = after.json()["wardens"]
    assert (warden["state"], warden["offline"], warden["missed_heartbeats"]) == (
        "ACTIVE",
        False,
        0,
    )
    assert warden["last_heartbeat_at"] is not None
    assert (warden["sub_bees"], warden["live_grants"]) == (2, 1)


async def test_a_submit_only_device_is_refused_the_warden_read() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        listed = await client.call(session, "GET", "/v1/wardens")

    assert listed.status_code == 403
