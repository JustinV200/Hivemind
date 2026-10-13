"""Tests for hivemind.swarm.registry.memory: what only the in-memory registry promises.

Fits into the Hive:
    Mirrors src/hivemind/swarm/registry/memory.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). Everything it shares with the SQLite registry is in the contract suite
    (tests/contracts/test_swarm_registry_contract.py). What is left is that it works with no trail
    at all, and that it hands out the stored values themselves.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.registry.memory for the module under test.
"""

from __future__ import annotations

from builders.cells import make_identity
from builders.swarm import make_device_party, make_swarm_invite, make_swarm_node

from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import InviteState
from hivemind.swarm.registry import MemorySwarmRegistry
from waggle.clock import FakeClock


async def test_without_a_trail_events_are_dropped_and_every_change_still_applies() -> None:
    clock = FakeClock()
    registry = MemorySwarmRegistry()
    recorder = SwarmRecorder(MemoryPheromoneTrail(clock), clock, make_identity(clock))
    invite = make_swarm_invite(clock=clock)
    node = make_swarm_node(make_device_party(clock), clock)
    event = recorder.swarm_event("swarm.enrolled", node.device_id, {"reason": "test"})

    await registry.put_invite(invite, event)
    await registry.redeem(invite.code_hash, node, clock.now(), event)

    spent = await registry.get_invite(invite.code_hash)
    assert spent is not None and spent.state is InviteState.REDEEMED
    assert await registry.node(node.device_id) == node


async def test_with_a_trail_the_events_land_on_it_in_the_order_the_changes_were_made() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = MemorySwarmRegistry(trail)
    recorder = SwarmRecorder(trail, clock, make_identity(clock))
    invite = make_swarm_invite(clock=clock)
    node = make_swarm_node(make_device_party(clock), clock)

    await registry.put_invite(invite, recorder.swarm_event("swarm.invited", node.device_id, {}))
    await registry.redeem(
        invite.code_hash,
        node,
        clock.now(),
        recorder.swarm_event("swarm.enrolled", node.device_id, {}),
    )

    kinds = [e.kind for e in await trail.query(TrailQuery())]
    assert kinds == ["swarm.invited", "swarm.enrolled"]
