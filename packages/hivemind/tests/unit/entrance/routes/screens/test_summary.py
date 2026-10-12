"""Test hivemind.entrance.routes.screens.summary: the strip's counters over a real listener.

A Hive with its one Warden, before and after that Warden's Heartbeat lists a running sub-bee, reads
the counters the Observation Hive's strip shows; a device without ``observe`` is refused.

Fits into the Hive:
    Mirrors src/hivemind/entrance/routes/screens/summary.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.screens import heartbeat
from builders.entrance.serving import ProgramGrant, serving

from waggle.messages.supervision import WorkerState

_OBSERVE = ProgramGrant(capabilities=("observe",))


async def test_the_strip_reads_the_core_counters_before_and_after_a_heartbeat() -> None:
    async with serving() as rig:
        client, session = await rig.program(_OBSERVE)
        link = rig.queen.wardens[0]
        before = await client.call(session, "GET", "/v1/summary")
        rig.telemetry.record(
            link.warden_id, heartbeat([("", WorkerState.RUNNING)]), rig.clock.now()
        )

        after = await client.call(session, "GET", "/v1/summary")

    assert before.status_code == after.status_code == 200, after.text
    assert (before.json()["active_bees"], before.json()["cells_inactive"]) == (0, 1)
    counters = after.json()
    assert (counters["active_bees"], counters["cells_active"], counters["cells_inactive"]) == (
        2,
        1,
        0,
    )
    assert (counters["cells_real"], counters["cells_virtual"]) == (1, 0)
    assert (counters["bees_awake"], counters["bees_autopilot"]) == (0, 0)


async def test_a_submit_only_device_is_refused_the_strip() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        refused = await client.call(session, "GET", "/v1/summary")

    assert refused.status_code == 403
