"""Contract suite for SwarmRegistry's runtime ladder: rung, wish and ceiling, over both stores.

Each test states one clause of the part of the `hivemind.swarm.registry.SwarmRegistry` contract
that moves a device along the runtime ladder (ADR-0052): `set_level` moves it one rung at a time
and records the event with the change, `set_desired_level` records the operator's pending wish,
and `set_ceiling` records the highest rung the Queen may take it to on her own (the operator's
pin). It runs against both implementations that ship (`swarm_registry_harness`). Split by feature
from test_swarm_registry_contract.py (codingrules 5.1).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Contract for the ladder half of
    hivemind.swarm.registry.protocol.SwarmRegistry.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.registry.protocol for the protocol under test.
    - packages/hivemind/tests/contracts/swarm_registry_harness.py for the rig.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from contracts.swarm_registry_harness import KINDS, Rig, open_rig

from hivemind.pheromone import TrailQuery
from hivemind.swarm.errors import LadderError
from waggle.messages.swarm import RuntimeLevel


@pytest.fixture(params=KINDS)
async def rig(request: pytest.FixtureRequest, tmp_path: Path) -> Rig:
    """A registry of the parametrised kind over a trail of the same kind, sharing one clock."""
    return await open_rig(request.param, tmp_path)


async def test_set_level_moves_one_rung_at_a_time_and_records_the_event(rig: Rig) -> None:
    node = await rig.enrol()
    event = rig.event("swarm.promoted", node.device_id)

    up = await rig.registry.set_level(node.device_id, RuntimeLevel.COLONIZED, event)

    assert up.level is RuntimeLevel.COLONIZED
    assert await rig.trail.query(TrailQuery()) == (event,)
    with pytest.raises(LadderError):  # Staying where it is is not a move.
        await rig.registry.set_level(node.device_id, RuntimeLevel.COLONIZED)
    nuc = await rig.registry.set_level(node.device_id, RuntimeLevel.NUC)
    down = await rig.registry.set_level(node.device_id, RuntimeLevel.COLONIZED)
    assert (nuc.level, down.level) == (RuntimeLevel.NUC, RuntimeLevel.COLONIZED)


async def test_a_rung_may_not_be_skipped(rig: Rig) -> None:
    node = await rig.enrol()

    with pytest.raises(LadderError):
        await rig.registry.set_level(node.device_id, RuntimeLevel.NUC)

    stored = await rig.registry.node(node.device_id)
    assert stored is not None and stored.level is RuntimeLevel.GATEWAY


async def test_reaching_the_requested_rung_clears_the_request_and_passing_another_keeps_it(
    rig: Rig,
) -> None:
    node = await rig.enrol()
    asked = await rig.registry.set_desired_level(node.device_id, RuntimeLevel.NUC)
    assert asked.desired_level is RuntimeLevel.NUC

    midway = await rig.registry.set_level(node.device_id, RuntimeLevel.COLONIZED)
    arrived = await rig.registry.set_level(node.device_id, RuntimeLevel.NUC)

    assert midway.desired_level is RuntimeLevel.NUC
    assert arrived.desired_level is None


async def test_asking_for_the_rung_the_node_is_on_asks_for_nothing_and_none_withdraws(
    rig: Rig,
) -> None:
    node = await rig.enrol()

    same = await rig.registry.set_desired_level(node.device_id, RuntimeLevel.GATEWAY)
    asked = await rig.registry.set_desired_level(node.device_id, RuntimeLevel.COLONIZED)
    withdrawn = await rig.registry.set_desired_level(node.device_id, None)

    assert same.desired_level is None
    assert asked.desired_level is RuntimeLevel.COLONIZED
    assert withdrawn.desired_level is None


async def test_a_ceiling_is_kept_cleared_and_may_equal_the_nodes_own_rung(rig: Rig) -> None:
    node = await rig.enrol()
    assert node.ceiling is None

    pinned = await rig.registry.set_ceiling(node.device_id, RuntimeLevel.GATEWAY)
    kept = await rig.registry.set_level(node.device_id, RuntimeLevel.COLONIZED)
    released = await rig.registry.set_ceiling(node.device_id, None)

    # A device sits on its ceiling (that is what a pin is), and moving it does not lift the pin.
    assert pinned.ceiling is RuntimeLevel.GATEWAY and kept.ceiling is RuntimeLevel.GATEWAY
    assert released.ceiling is None
    assert await rig.registry.node(node.device_id) == released
