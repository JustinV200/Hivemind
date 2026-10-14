"""Tests for hivemind.queen.swarm_gate.colonize.events: the events a colonization writes.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/events.py (codingrules section 3). The recorder
    and trail are the real in-memory ones; a built event is checked for what it says, and that
    building one writes nothing.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.events for the module under test.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for what each event means.
"""

from __future__ import annotations

from builders.swarm import GateRig

from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate.colonize.events import level_event, record_migrated, undone_event
from hivemind.swarm import RuntimeLevel


async def test_climbing_a_rung_is_a_promotion_naming_both_rungs(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.GATEWAY)

    event = level_event(rig.hive.recorder, node, RuntimeLevel.COLONIZED, "The operator asked.")

    assert event.kind == "swarm.promoted" and event.subject_id == node.device_id
    assert event.payload == {
        "device_id": node.device_id,
        "node_id": node.node_id,
        "cell_id": node.cell_id,
        "from_level": "GATEWAY",
        "to_level": "COLONIZED",
        "reason": "The operator asked.",
    }


async def test_lowering_a_rung_is_a_demotion(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)

    event = level_event(rig.hive.recorder, node, RuntimeLevel.GATEWAY, "Withdrawn.")

    assert event.kind == "swarm.demoted"
    assert (event.payload["from_level"], event.payload["to_level"]) == ("COLONIZED", "GATEWAY")


async def test_an_undone_colonization_is_a_demotion_back_to_the_rung_the_device_never_left(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.GATEWAY)

    event = undone_event(rig.hive.recorder, node, "start_failed")

    assert event.kind == "swarm.demoted"
    assert (event.payload["from_level"], event.payload["to_level"]) == ("COLONIZED", "GATEWAY")
    assert event.payload["reason"] == "start_failed"  # A stable word, nothing a device wrote.


async def test_building_an_event_writes_nothing(rig: GateRig) -> None:
    node = await rig.enrol()

    level_event(rig.hive.recorder, node, RuntimeLevel.COLONIZED, "Why.")
    undone_event(rig.hive.recorder, node, "word")

    assert await rig.hive.events() == []


async def test_a_migration_is_recorded_as_the_same_warden_changing_nodes(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    deps = rig.queen_deps

    await record_migrated(deps, node, deps.identity.node_id)

    [event] = await deps.trail.query(TrailQuery(kind="warden.migrated"))
    assert event.subject_id == node.warden_id  # One Warden before and after.
    assert event.payload == {
        "cell_id": node.cell_id,
        "device_id": node.device_id,
        "from_node": deps.identity.node_id,
        "to_node": node.node_id,
    }
    assert event.node_id == deps.identity.node_id  # The Queen's record, not the Warden's.
