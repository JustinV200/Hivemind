"""Tests for hivemind.queen.swarm_gate.colonize.renew: the directories a Warden watches are a term.

A colonized device's Warden reads the logs and file changes of the directories the operator named
for it (`hive swarm watch`), as the packet handed them to it when it started it. It has no message
that changes them, so naming others is renewed like a new access level or tier: the packet is told,
the Warden is stopped and started again, and its `cell.ready` must name the directories now held. A
Warden that comes up under the old ones is never trusted, because the operator's directories did not
reach it, and the device falls back to Level 0, where the Hive Stand's Warden asks the packet for
exactly the directories the registry holds.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/renew/flow.py and drift.py (codingrules section
    3), split by feature (14.2) from test_flow.py and test_flow_fallback.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.renew.flow for the module under test.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the choice of carrier.
"""

from __future__ import annotations

from builders.swarm import GateRig, make_renewal

from hivemind.pheromone import TrailQuery
from hivemind.swarm import RuntimeLevel

_ROOTS = ("/var/log/app", "/srv/data")


async def test_naming_the_directories_a_warden_watches_restarts_it_under_them(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.rewatch(_ROOTS)

        assert await scenario.renew()  # Nothing changed but the directories, and it is renewed.
        await scenario.finish()

        assert scenario.packet.log == ["update", "colonize:WITHDRAW", "colonize:START"]
        [update] = scenario.packet.updates
        assert update.watch_roots == _ROOTS  # The packet is told, so it can hand them over.
        assert not await scenario.renew()  # The new Warden reported the directories it holds.
        assert (await scenario.node()).level is not RuntimeLevel.GATEWAY  # Renewed, not lowered.
        await scenario.close()


async def test_clearing_the_directories_reaches_the_warden_the_same_way(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.rewatch(_ROOTS)
        assert await scenario.renew()
        await scenario.finish()

        await scenario.rewatch(())  # `hive swarm watch --clear`

        assert await scenario.renew()
        await scenario.finish()
        [_, update] = scenario.packet.updates
        assert update.watch_roots == ()
        assert not await scenario.renew()
        await scenario.close()


async def test_the_running_task_is_checkpointed_and_resumed_by_the_restart(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.rewatch(_ROOTS)

        assert await scenario.renew()
        await scenario.finish()

        # The same promise as for a new level: a bee is paused at a Handoff before its Warden goes.
        assert [pause.task_id for pause in scenario.first.pauses] == [scenario.task.id]
        await scenario.close()


async def test_a_warden_that_comes_up_under_the_old_directories_is_never_trusted(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_renewal(rig)
        await scenario.rewatch(_ROOTS)
        scenario.stale_terms = True  # The packet started it from directories it was never sent.

        assert await scenario.renew()
        await scenario.finish()

        row = await scenario.node()
        assert row.level is RuntimeLevel.GATEWAY  # Where the registry's directories are enforced.
        events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
        details = [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]
        assert ("renew_failed", "terms_not_applied") in details
        [stale] = scenario.started
        assert not stale.is_connected
        await scenario.close()
