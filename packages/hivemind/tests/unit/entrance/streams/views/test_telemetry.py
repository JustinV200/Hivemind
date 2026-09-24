"""Test hivemind.entrance.streams.views.telemetry: every bee's telemetry, from her Heartbeats.

Over a real listener and a real, running Queen: a Heartbeat her Warden sends reaches the view
through her ``on_heartbeat`` hook as one sample for the Warden and one per sub-bee row; the view
is refused to a device without ``observe:thoughts`` and C2 (a sample is a bee's own words); and a
subscriber further behind than its backlog is closed with FELL_BEHIND.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/views/telemetry.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio

from builders.entrance.serving import ProgramGrant, RigOptions, serving
from builders.entrance.views import close_code, next_frame, open_view, refusal
from builders.supervision import make_telemetry

from hivemind.entrance.streams import CloseReason
from waggle.ids import new_worker_id
from waggle.messages.supervision import ChildTelemetry, Heartbeat, WardenState, WorkerState

_THOUGHTS = ProgramGrant(capabilities=("observe", "observe:thoughts", "honey:clearance:c2"))
_PATH = "/v1/telemetry/stream"


def _heartbeat(children: tuple[ChildTelemetry, ...] = ()) -> Heartbeat:
    """A Warden's Heartbeat: its own telemetry and one row per sub-bee."""
    return Heartbeat(
        telemetry=make_telemetry(goal="Keep the garden page tidy."),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=children,
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


async def test_a_heartbeat_the_queen_receives_is_sent_as_a_sample_per_bee() -> None:
    async with serving() as rig:
        client, session = await rig.program(_THOUGHTS)
        warden_id = rig.queen.wardens[0].warden_id
        target = f"{_PATH}?warden_id={warden_id}"
        socket = await open_view(rig, client, session, target, lambda: rig.telemetry.subscribers)
        child = ChildTelemetry(
            worker_id=new_worker_id(rig.clock),
            task_id=None,
            state=WorkerState.RUNNING,
            telemetry=make_telemetry(tokens_used=4_096),
        )
        running = asyncio.ensure_future(rig.queen.run())
        try:
            await rig.warden_end.send(_heartbeat((child,)))
            frames = [(await next_frame(socket))["sample"] for _ in range(2)]
        finally:
            await socket.close()
            await rig.queen.stop()
            await running

    assert [sample["warden_id"] for sample in frames] == [warden_id, warden_id]
    assert [sample["worker_id"] for sample in frames] == [None, child.worker_id]
    assert (frames[0]["warden_state"], frames[1]["worker_state"]) == ("ACTIVE", "RUNNING")
    assert (frames[0]["goal"], frames[1]["tokens_used"]) == ("Keep the garden page tidy.", 4_096)


async def test_the_view_is_refused_without_thoughts_and_c2() -> None:
    async with serving() as rig:
        observer, observer_session = await rig.program(ProgramGrant(capabilities=("observe",)))
        uncleared, uncleared_session = await rig.program(
            ProgramGrant(capabilities=("observe", "observe:thoughts"))
        )

        codes = [
            await refusal(rig, observer, observer_session, _PATH),
            await refusal(rig, uncleared, uncleared_session, _PATH),
        ]

    assert codes == [CloseReason.FORBIDDEN.code] * 2


async def test_a_subscriber_further_behind_than_its_backlog_is_closed_as_fell_behind() -> None:
    async with serving(RigOptions(stream_backlog=2)) as rig:
        client, session = await rig.program(_THOUGHTS)
        socket = await open_view(rig, client, session, _PATH, lambda: rig.telemetry.subscribers)
        warden_id = rig.queen.wardens[0].warden_id

        # Three Heartbeats in one go, as a busy tick hands them over: one past the backlog.
        for _ in range(3):
            rig.telemetry.record(warden_id, _heartbeat(), rig.clock.now())
        code = await close_code(socket)

    assert code == CloseReason.FELL_BEHIND.code
    assert rig.telemetry.subscribers == 0
