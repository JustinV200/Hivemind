"""Tests for hivemind.swarm.operator.desk: naming the directories a device's Warden watches.

Fits into the Hive:
    Mirrors src/hivemind/swarm/operator/desk.py (codingrules section 3); split by feature (14.2)
    from test_desk.py, which holds the desk's other acts. The desk runs over the in-memory
    registry writing to an in-memory trail, so each test reads back what a running Hive would find:
    the node the registry holds and the events on the trail. What the directories may be is
    judged in test_roots.py; this module holds what the act does with them.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.operator.desk for the module under test.
    - hivemind.swarm.operator.roots for the rule that judges the directories.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from builders.cells import make_identity
from builders.swarm import enrol_node, make_device_party, make_swarm_node

from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent, TrailQuery
from hivemind.swarm.errors import NodeRevokedError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.operator import OperatorDesk, WatchRootRefusedError
from hivemind.swarm.registry import MemorySwarmRegistry
from waggle.clock import FakeClock


@dataclass
class _Rig:
    """The desk, the registry it writes and the trail the events land on."""

    clock: FakeClock
    registry: MemorySwarmRegistry
    trail: MemoryPheromoneTrail
    desk: OperatorDesk

    async def enrol(self, **overrides: object) -> SwarmNode:
        """Enrol a fresh device with these terms and return it."""
        node = make_swarm_node(make_device_party(self.clock), self.clock, **overrides)
        return await enrol_node(self.registry, node, self.clock)

    async def events(self) -> list[PheromoneEvent]:
        """Every `swarm.watch_changed` event on the trail, oldest first."""
        return list(await self.trail.query(TrailQuery(kind="swarm.watch_changed")))

    async def stored(self, node: SwarmNode) -> SwarmNode:
        """The node as the registry holds it now."""
        found = await self.registry.node(node.device_id)
        assert found is not None
        return found


def _rig() -> _Rig:
    """A desk over a fresh registry and trail; the operator's acts are recorded as `human`."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = MemorySwarmRegistry(trail)
    recorder = SwarmRecorder(trail, clock, make_identity(clock, actor="human"))
    return _Rig(clock, registry, trail, OperatorDesk(registry, recorder))


async def test_the_directories_are_stored_in_their_normal_form_and_the_act_is_recorded() -> None:
    rig = _rig()
    node = await rig.enrol(label="kitchen-pi")

    change = await rig.desk.set_watch_roots(node, ["/var/log/app/", "/srv//data"])

    assert change.is_changed and change.node.watch_roots == ("/var/log/app", "/srv/data")
    assert (await rig.stored(node)).watch_roots == ("/var/log/app", "/srv/data")
    [event] = await rig.events()
    assert event.actor == "human" and event.subject_id == node.device_id
    assert event.payload == {
        "device_id": node.device_id,
        "cell_id": node.cell_id,
        "from_roots": 0,
        "roots": ["/var/log/app", "/srv/data"],
    }


async def test_naming_exactly_the_directories_it_already_has_writes_nothing() -> None:
    rig = _rig()
    node = await rig.enrol(watch_roots=("/var/log/app",))

    change = await rig.desk.set_watch_roots(node, ["/var/log/app/"])  # The same, spelled loosely.

    assert not change.is_changed and change.node == await rig.stored(node)
    assert await rig.events() == []


async def test_naming_none_stops_the_file_watching_and_says_how_many_it_had() -> None:
    rig = _rig()
    node = await rig.enrol(watch_roots=("/a", "/b", "/c"))

    change = await rig.desk.set_watch_roots(node, [])

    assert change.is_changed and change.node.watch_roots == ()
    [event] = await rig.events()
    assert (event.payload["from_roots"], event.payload["roots"]) == (3, [])


async def test_a_new_list_replaces_the_old_one() -> None:
    rig = _rig()
    node = await rig.enrol(watch_roots=("/old",))

    change = await rig.desk.set_watch_roots(node, ["/new"])

    assert change.node.watch_roots == ("/new",)


async def test_a_refused_list_writes_and_records_nothing() -> None:
    rig = _rig()
    node = await rig.enrol(watch_roots=("/kept",))

    with pytest.raises(WatchRootRefusedError):
        await rig.desk.set_watch_roots(node, ["/fine", "not/absolute"])

    assert (await rig.stored(node)).watch_roots == ("/kept",)
    assert await rig.events() == []


async def test_a_revoked_device_accepts_no_directories() -> None:
    rig = _rig()
    node = await rig.enrol(status=NodeStatus.ONLINE)
    await rig.desk.revoke(node)

    with pytest.raises(NodeRevokedError):
        await rig.desk.set_watch_roots(node, ["/var/log"])

    assert await rig.events() == []


async def test_the_desk_judges_against_the_device_as_the_registry_holds_it_now() -> None:
    rig = _rig()
    node = await rig.enrol()
    # A stale reference: the registry's node has narrower capabilities than the one the operator
    # looked at, so the directory is judged on what is true now.
    await rig.registry.set_access(node.device_id, node.access_level, ("fs:read:/srv/ok/**",))

    with pytest.raises(WatchRootRefusedError):
        await rig.desk.set_watch_roots(node, ["/srv/elsewhere"])
