"""Tests for hivemind.swarm.operator.desk: the operator's acts on devices, and their trail events.

Fits into the Hive:
    Mirrors src/hivemind/swarm/operator/desk.py (codingrules section 3). The desk runs over the
    in-memory registry (the fake the contract suite holds to the SQLite one) writing to an
    in-memory trail, so each test reads back exactly what a running Hive would find: the node the
    registry holds and the events on the trail. The properties are the desk's own: an act is
    written with its event, a repeat writes nothing, a revoked device accepts nothing, a rung
    request is one step, and a revocation survives a status that moved under it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.operator.desk for the module under test.
    - tests/contracts/test_swarm_registry_contract.py for the registry writes the desk relies on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest
from builders.cells import make_identity
from builders.swarm import enrol_node, make_device_party, make_swarm_node

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent, SwarmEvent, TrailQuery
from hivemind.swarm.errors import NodeNotFoundError, NodeRevokedError, NodeStatusConflictError
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.operator import OperatorDesk, RungUnavailableError, TierRefusedError
from hivemind.swarm.operator.desk import STATUS_ATTEMPTS
from hivemind.swarm.registry import MemorySwarmRegistry
from waggle.clock import FakeClock
from waggle.ids import DeviceId
from waggle.messages.swarm import RuntimeLevel


class _FlappingRegistry(MemorySwarmRegistry):
    """A registry whose device's status moves under the first `flaps` status writes.

    Each flap is the heartbeat monitor winning the race: the node goes ONLINE to UNREACHABLE (or
    back) between the desk's read and its compare-and-set, so the write sees a stale status.
    """

    def __init__(self, trail: MemoryPheromoneTrail, flaps: int) -> None:
        """Build the registry; `flaps` status writes will find the status already moved."""
        super().__init__(trail)
        self.flaps = flaps

    async def set_status(
        self,
        device_id: DeviceId,
        expected: NodeStatus,
        new: NodeStatus,
        at: datetime,
        event: SwarmEvent | None = None,
    ) -> SwarmNode:
        """Move the status the monitor's way first, while flaps remain, then try the caller's."""
        if self.flaps > 0:
            self.flaps -= 1
            other = NodeStatus.UNREACHABLE if expected is NodeStatus.ONLINE else NodeStatus.ONLINE
            await super().set_status(device_id, expected, other, at)
        return await super().set_status(device_id, expected, new, at, event)


class _RecordingRegistry(MemorySwarmRegistry):
    """A registry that remembers the order in which a rung request's two writes arrived."""

    def __init__(self, trail: MemoryPheromoneTrail) -> None:
        """Build the registry with nothing written yet."""
        super().__init__(trail)
        self.writes: list[str] = []

    async def set_ceiling(self, device_id: DeviceId, ceiling: RuntimeLevel | None) -> SwarmNode:
        """Note the ceiling write, then make it."""
        self.writes.append("ceiling")
        return await super().set_ceiling(device_id, ceiling)

    async def set_desired_level(self, device_id: DeviceId, level: RuntimeLevel | None) -> SwarmNode:
        """Note the wish write, then make it."""
        self.writes.append("wish")
        return await super().set_desired_level(device_id, level)


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

    async def events(self, kind: str) -> list[PheromoneEvent]:
        """Every event of `kind` on the trail, oldest first."""
        return list(await self.trail.query(TrailQuery(kind=kind)))

    async def stored(self, node: SwarmNode) -> SwarmNode:
        """The node as the registry holds it now."""
        found = await self.registry.node(node.device_id)
        assert found is not None
        return found


def _rig(flaps: int = 0) -> _Rig:
    """A desk over a fresh registry and trail; the operator's acts are recorded as `human`."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = _FlappingRegistry(trail, flaps) if flaps else MemorySwarmRegistry(trail)
    recorder = SwarmRecorder(trail, clock, make_identity(clock, actor="human"))
    return _Rig(clock, registry, trail, OperatorDesk(registry, recorder))


async def test_revoke_makes_the_enrolment_final_and_records_who_did_it() -> None:
    rig = _rig()
    node = await rig.enrol(label="kitchen-pi")

    change = await rig.desk.revoke(node)

    assert change.is_changed and change.node.status is NodeStatus.REVOKED
    assert (await rig.stored(node)).revoked_at == rig.clock.now()
    [event] = await rig.events("swarm.revoked")
    assert isinstance(event, SwarmEvent) and event.actor == "human"
    assert event.subject_id == node.device_id
    assert event.payload == {
        "device_id": node.device_id,
        "cell_id": node.cell_id,
        "from_status": "ONLINE",
        "reason": "operator",
    }


async def test_revoke_again_changes_nothing_and_records_nothing() -> None:
    rig = _rig()
    node = await rig.enrol()
    await rig.desk.revoke(node)

    again = await rig.desk.revoke(node)

    assert not again.is_changed and again.node.status is NodeStatus.REVOKED
    assert len(await rig.events("swarm.revoked")) == 1


async def test_revoke_reads_the_status_again_when_it_moved_under_the_first_attempt() -> None:
    rig = _rig(flaps=STATUS_ATTEMPTS - 1)
    node = await rig.enrol()

    change = await rig.desk.revoke(node)

    # The monitor flipped the status twice before the third try, which was made on what it found.
    assert change.is_changed and change.node.status is NodeStatus.REVOKED
    [event] = await rig.events("swarm.revoked")
    assert event.payload["from_status"] == "ONLINE"


async def test_revoke_gives_up_with_the_conflict_when_the_status_never_settles() -> None:
    rig = _rig(flaps=STATUS_ATTEMPTS)
    node = await rig.enrol()

    with pytest.raises(NodeStatusConflictError):
        await rig.desk.revoke(node)

    assert (await rig.stored(node)).status is not NodeStatus.REVOKED
    assert await rig.events("swarm.revoked") == []


async def test_revoke_of_a_device_that_left_the_registry_is_refused() -> None:
    rig = _rig()
    gone = make_swarm_node(make_device_party(rig.clock), rig.clock)

    with pytest.raises(NodeNotFoundError):
        await rig.desk.revoke(gone)


async def test_set_access_regrants_the_capabilities_of_the_new_level_and_records_it() -> None:
    rig = _rig()
    node = await rig.enrol(access_level=AccessLevel.FULL)

    change = await rig.desk.set_access(node, AccessLevel.READ_ONLY)

    assert change.is_changed and change.node.access_level is AccessLevel.READ_ONLY
    # A read-only device is told to read and to be watched, and to run and write nothing.
    assert change.node.granted_capabilities == ("fs:read:**", f"watch:{node.cell_id}")
    [event] = await rig.events("swarm.access_changed")
    assert event.actor == "human" and event.subject_id == node.device_id
    assert (event.payload["from_level"], event.payload["to_level"]) == ("FULL", "READ_ONLY")


async def test_set_access_may_grant_more_than_the_device_holds() -> None:
    rig = _rig()
    node = await rig.enrol(access_level=AccessLevel.SCRATCH)

    change = await rig.desk.set_access(node, AccessLevel.FULL)

    assert change.node.access_level is AccessLevel.FULL
    assert "cell:outside_scratch:**" in change.node.granted_capabilities


async def test_set_access_to_the_level_it_already_has_writes_nothing() -> None:
    rig = _rig()
    node = await rig.enrol(access_level=AccessLevel.SCRATCH)

    change = await rig.desk.set_access(node, AccessLevel.SCRATCH)

    assert not change.is_changed and change.node == await rig.stored(node)
    assert await rig.events("swarm.access_changed") == []


async def test_set_shield_moves_the_device_to_propolis_and_records_it() -> None:
    rig = _rig()
    node = await rig.enrol()

    change = await rig.desk.set_shield(node, CombShieldLevel.PROPOLIS)

    assert change.is_changed and change.node.comb_shield is CombShieldLevel.PROPOLIS
    [event] = await rig.events("swarm.shield_changed")
    assert event.actor == "human"
    assert (event.payload["from_tier"], event.payload["to_tier"]) == ("MEADOW", "PROPOLIS")


async def test_set_shield_to_the_tier_it_already_has_writes_nothing() -> None:
    rig = _rig()
    node = await rig.enrol()

    change = await rig.desk.set_shield(node, CombShieldLevel.MEADOW)

    assert not change.is_changed
    assert await rig.events("swarm.shield_changed") == []


async def test_set_shield_refuses_night_veil_with_the_reason_and_changes_nothing() -> None:
    rig = _rig()
    node = await rig.enrol()

    with pytest.raises(TierRefusedError) as refused:
        await rig.desk.set_shield(node, CombShieldLevel.NIGHT_VEIL)

    assert "Virtual Cells" in str(refused.value) and "never torn down" in str(refused.value)
    assert (await rig.stored(node)).comb_shield is CombShieldLevel.MEADOW
    assert await rig.events("swarm.shield_changed") == []


@pytest.mark.parametrize(
    ("level", "promote", "expected"),
    [
        (RuntimeLevel.GATEWAY, True, RuntimeLevel.COLONIZED),
        (RuntimeLevel.COLONIZED, True, RuntimeLevel.NUC),
        (RuntimeLevel.COLONIZED, False, RuntimeLevel.GATEWAY),
        (RuntimeLevel.NUC, False, RuntimeLevel.COLONIZED),
    ],
)
async def test_move_asks_for_the_adjacent_rung_and_records_nothing_yet(
    level: RuntimeLevel, promote: bool, expected: RuntimeLevel
) -> None:
    rig = _rig()
    node = await rig.enrol(level=level)

    change = await rig.desk.move(node, promote=promote)

    assert change.is_changed and change.node.desired_level is expected
    assert change.node.level is level  # Only the wish is written; the Queen moves the device.
    # A climb lets the Queen climb further herself; a descent pins the device where it lands.
    assert change.node.ceiling is (None if promote else expected)
    assert await rig.trail.query(TrailQuery(family="swarm")) == ()


async def test_move_asked_twice_is_one_wish() -> None:
    rig = _rig()
    node = await rig.enrol()
    await rig.desk.move(node, promote=True)

    again = await rig.desk.move(node, promote=True)

    assert not again.is_changed and again.node.desired_level is RuntimeLevel.COLONIZED


async def test_move_the_other_way_replaces_the_pending_wish() -> None:
    rig = _rig()
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    await rig.desk.move(node, promote=True)

    changed = await rig.desk.move(node, promote=False)

    assert changed.is_changed and changed.node.desired_level is RuntimeLevel.GATEWAY


async def test_promote_past_the_top_of_the_ladder_is_refused_with_the_reason() -> None:
    rig = _rig()
    node = await rig.enrol(level=RuntimeLevel.NUC)

    with pytest.raises(RungUnavailableError) as refused:
        await rig.desk.move(node, promote=True)

    assert "the top of the ladder" in str(refused.value)
    stored = await rig.stored(node)
    assert stored.desired_level is None and stored.ceiling is None


async def test_demote_of_a_gateway_pins_it_there_and_asks_for_nothing() -> None:
    rig = _rig()
    node = await rig.enrol()

    pinned = await rig.desk.move(node, promote=False)

    assert pinned.is_changed
    assert pinned.node.ceiling is RuntimeLevel.GATEWAY and pinned.node.desired_level is None
    assert await rig.trail.query(TrailQuery(family="swarm")) == ()


async def test_demote_of_a_pinned_gateway_changes_nothing() -> None:
    rig = _rig()
    node = await rig.enrol()
    await rig.desk.move(node, promote=False)

    again = await rig.desk.move(node, promote=False)

    assert not again.is_changed and again.node.ceiling is RuntimeLevel.GATEWAY


async def test_demote_of_a_gateway_withdraws_a_pending_climb() -> None:
    rig = _rig()
    node = await rig.enrol()
    await rig.desk.move(node, promote=True)

    pinned = await rig.desk.move(node, promote=False)

    # The operator now says the opposite: the wish to climb goes with the pin.
    assert pinned.node.desired_level is None and pinned.node.ceiling is RuntimeLevel.GATEWAY


async def test_promote_lifts_the_pin_it_is_asked_over() -> None:
    rig = _rig()
    node = await rig.enrol()
    await rig.desk.move(node, promote=False)

    climbing = await rig.desk.move(node, promote=True)

    assert climbing.is_changed
    assert climbing.node.ceiling is None and climbing.node.desired_level is RuntimeLevel.COLONIZED


async def test_demote_sets_the_ceiling_before_it_writes_the_wish() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = _RecordingRegistry(trail)
    recorder = SwarmRecorder(trail, clock, make_identity(clock, actor="human"))
    rig = _Rig(clock, registry, trail, OperatorDesk(registry, recorder))
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)

    await rig.desk.move(node, promote=False)

    # A Queen reading between the two writes must already find the device held.
    assert registry.writes == ["ceiling", "wish"]


async def test_a_revoked_device_accepts_no_change() -> None:
    rig = _rig()
    node = await rig.enrol()
    await rig.desk.revoke(node)

    with pytest.raises(NodeRevokedError):
        await rig.desk.set_access(node, AccessLevel.READ_ONLY)
    with pytest.raises(NodeRevokedError):
        await rig.desk.set_shield(node, CombShieldLevel.PROPOLIS)
    with pytest.raises(NodeRevokedError):
        await rig.desk.move(node, promote=True)

    assert await rig.events("swarm.access_changed") == []
    assert await rig.events("swarm.shield_changed") == []


async def test_find_resolves_a_label_through_the_registry() -> None:
    rig = _rig()
    node = await rig.enrol(label="attic-pc")

    assert (await rig.desk.find("attic-pc")).device_id == node.device_id
