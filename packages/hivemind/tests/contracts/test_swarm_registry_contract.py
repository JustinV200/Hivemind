"""Contract suite for SwarmRegistry: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.swarm.registry.SwarmRegistry contract and runs against both implementations that
    ship: MemorySwarmRegistry (over MemoryPheromoneTrail) and SqliteSwarmRegistry (over
    SqlitePheromoneTrail, both on the same tmp_path SQLite file). A new implementation joins the
    fixture's params and must pass here before it is used anywhere else (codingrules 14.3). The
    two clauses that matter most are the ones a security review asks about first: an invite is
    spent by exactly one device even when two present it at once, and a change and its trail
    event are committed together or not at all.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.registry.protocol for the protocol under test.
    - packages/hivemind/tests/contracts/test_leavings_store_contract.py for the pattern this
      mirrors.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from builders.cells import make_capabilities, make_identity
from builders.forage import make_capacity
from builders.swarm import (
    make_device_party,
    make_swarm_invite,
    make_swarm_node,
)
from pydantic import ValidationError

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.common.sqlite import connect
from hivemind.pheromone import (
    DuplicateEventError,
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    SwarmEvent,
    TrailQuery,
)
from hivemind.swarm.enrolment import new_invite_token, token_hash
from hivemind.swarm.errors import (
    EnrolmentDeniedError,
    EnrolRefusal,
    InvalidNodeTransitionError,
    InviteConflictError,
    InviteNotFoundError,
    LadderError,
    NodeNotFoundError,
    NodeRevokedError,
    NodeStatusConflictError,
)
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import InviteState, SwarmInvite, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import MemorySwarmRegistry, SqliteSwarmRegistry, SwarmRegistry
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_device_id, new_node_id
from waggle.messages.swarm import RuntimeLevel

_KINDS = ("memory", "sqlite")


@dataclass(frozen=True, slots=True)
class _Rig:
    """A registry, the trail its events land on and the clock they are stamped with."""

    registry: SwarmRegistry
    trail: PheromoneTrail
    recorder: SwarmRecorder
    clock: FakeClock

    def event(self, kind: str = "swarm.online", subject: str | None = None) -> SwarmEvent:
        """A fresh, valid swarm event about `subject` (a new device when omitted)."""
        about = subject if subject is not None else new_device_id(self.clock)
        return self.recorder.swarm_event(kind, about, {"reason": "test"})

    async def kinds(self) -> list[str]:
        """The kind of every event on the trail, oldest first."""
        return [e.kind for e in await self.trail.query(TrailQuery())]

    async def enrol(self, **node_changes: object) -> SwarmNode:
        """Enrol a fresh ONLINE node through a fresh invite; return the stored node."""
        invite = make_swarm_invite(clock=self.clock)
        await self.registry.put_invite(invite)
        node = make_swarm_node(make_device_party(self.clock), self.clock, **node_changes)
        return await self.registry.redeem(invite.code_hash, node, self.clock.now())

    async def issue(self, **invite_changes: object) -> SwarmInvite:
        """Store a fresh ISSUED invite; return it."""
        invite = make_swarm_invite(None, self.clock, **invite_changes)
        await self.registry.put_invite(invite)
        return invite


@pytest.fixture(params=_KINDS)
async def rig(request: pytest.FixtureRequest, tmp_path: Path) -> _Rig:
    """A registry of the parametrised kind over a trail of the same kind, sharing one clock."""
    clock = FakeClock()
    identity = make_identity(clock)
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return _Rig(
            MemorySwarmRegistry(memory_trail),
            memory_trail,
            SwarmRecorder(memory_trail, clock, identity),
            clock,
        )
    # SQLite: two connections to the same file (ADR-0006 decision 1); the trail's migration must
    # run before the registry's own, whose events are written into the trail's table.
    path = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(path), clock)
    registry = await SqliteSwarmRegistry.create(connect(path), clock)
    return _Rig(registry, sqlite_trail, SwarmRecorder(sqlite_trail, clock, identity), clock)


# ──────────────────────────────────────────────────────────────────────────────
# Invites: storing, listing, expiring, revoking
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_stored_invite_is_found_by_its_hash_and_equal_to_what_was_stored(
    rig: _Rig,
) -> None:
    invite = await rig.issue(label="desk", comb_shield=CombShieldLevel.PROPOLIS)

    assert await rig.registry.get_invite(invite.code_hash) == invite


async def test_an_unknown_hash_finds_no_invite(rig: _Rig) -> None:
    assert await rig.registry.get_invite(token_hash(new_invite_token())) is None


async def test_put_invite_records_its_event_with_the_row(rig: _Rig) -> None:
    event = rig.event("swarm.invited")

    await rig.registry.put_invite(make_swarm_invite(clock=rig.clock), event)

    assert await rig.trail.query(TrailQuery()) == (event,)


async def test_putting_a_duplicate_hash_is_refused_and_records_nothing(rig: _Rig) -> None:
    invite = await rig.issue()

    with pytest.raises(InviteConflictError):
        await rig.registry.put_invite(invite, rig.event("swarm.invited"))

    assert await rig.kinds() == [] and len(await rig.registry.invites()) == 1


async def test_putting_an_invite_that_is_not_issued_is_refused(rig: _Rig) -> None:
    with pytest.raises(InviteConflictError):
        await rig.registry.put_invite(make_swarm_invite(clock=rig.clock, state=InviteState.REVOKED))


async def test_invites_are_listed_oldest_first_and_filtered_by_state(rig: _Rig) -> None:
    first = await rig.issue()
    rig.clock.advance(1.0)
    second = await rig.issue()
    await rig.registry.revoke_invite(first.code_hash)

    everything = await rig.registry.invites()
    issued = await rig.registry.invites(InviteState.ISSUED)
    revoked = await rig.registry.invites(InviteState.REVOKED)

    assert [i.code_hash for i in everything] == [first.code_hash, second.code_hash]
    assert [i.code_hash for i in issued] == [second.code_hash]
    assert [i.code_hash for i in revoked] == [first.code_hash]
    assert await rig.registry.invites(InviteState.EXPIRED) == ()


async def test_expire_invites_moves_only_the_lapsed_issued_ones_and_counts_them(rig: _Rig) -> None:
    lapsing = await rig.issue()
    spent = await rig.issue()
    await rig.registry.redeem(
        spent.code_hash, make_swarm_node(make_device_party(rig.clock), rig.clock), rig.clock.now()
    )
    fresh = await rig.issue(expires_at=rig.clock.now() + timedelta(hours=2))

    moved = await rig.registry.expire_invites(lapsing.expires_at)  # Lapsed exactly at its expiry.

    assert moved == 1
    states = {i.code_hash: i.state for i in await rig.registry.invites()}
    assert states == {
        lapsing.code_hash: InviteState.EXPIRED,
        spent.code_hash: InviteState.REDEEMED,
        fresh.code_hash: InviteState.ISSUED,
    }
    assert await rig.registry.expire_invites(lapsing.expires_at) == 0


async def test_revoke_invite_cancels_an_issued_invite_and_records_the_event(rig: _Rig) -> None:
    invite = await rig.issue()
    event = rig.event("swarm.revoked")

    revoked = await rig.registry.revoke_invite(invite.code_hash, event)

    assert revoked.state is InviteState.REVOKED
    assert await rig.registry.get_invite(invite.code_hash) == revoked
    assert await rig.trail.query(TrailQuery()) == (event,)


async def test_revoking_an_unknown_invite_raises_not_found(rig: _Rig) -> None:
    with pytest.raises(InviteNotFoundError):
        await rig.registry.revoke_invite(token_hash(new_invite_token()))


async def test_revoking_an_invite_that_is_no_longer_issued_is_refused_and_records_nothing(
    rig: _Rig,
) -> None:
    invite = await rig.issue()
    await rig.registry.revoke_invite(invite.code_hash)

    with pytest.raises(InviteConflictError):
        await rig.registry.revoke_invite(invite.code_hash, rig.event())

    assert await rig.kinds() == []


async def test_an_event_that_cannot_be_recorded_leaves_the_invite_issued(rig: _Rig) -> None:
    invite = await rig.issue()
    event = rig.event()
    await rig.trail.record(event)  # The same id again will be refused as a duplicate.

    with pytest.raises(DuplicateEventError):
        await rig.registry.revoke_invite(invite.code_hash, event)

    stored = await rig.registry.get_invite(invite.code_hash)
    assert stored is not None and stored.state is InviteState.ISSUED


# ──────────────────────────────────────────────────────────────────────────────
# Redemption: the atomic, single-use step
# ──────────────────────────────────────────────────────────────────────────────


async def test_redeeming_creates_the_node_and_spends_the_invite_for_that_device(rig: _Rig) -> None:
    invite = await rig.issue()
    node = make_swarm_node(make_device_party(rig.clock), rig.clock)

    stored = await rig.registry.redeem(invite.code_hash, node, rig.clock.now())

    spent = await rig.registry.get_invite(invite.code_hash)
    assert stored == node and await rig.registry.node(node.device_id) == node
    assert spent is not None and spent.state is InviteState.REDEEMED
    assert spent.device_id == node.device_id


async def test_redeeming_records_its_event_together_with_the_change(rig: _Rig) -> None:
    invite = await rig.issue()
    event = rig.event("swarm.enrolled")

    await rig.registry.redeem(
        invite.code_hash,
        make_swarm_node(make_device_party(rig.clock), rig.clock),
        rig.clock.now(),
        event,
    )

    assert await rig.trail.query(TrailQuery()) == (event,)


async def test_a_spent_invite_admits_no_second_device(rig: _Rig) -> None:
    invite = await rig.issue()
    first = make_swarm_node(make_device_party(rig.clock), rig.clock)
    second = make_swarm_node(make_device_party(rig.clock), rig.clock)
    await rig.registry.redeem(invite.code_hash, first, rig.clock.now())

    with pytest.raises(EnrolmentDeniedError) as caught:
        await rig.registry.redeem(invite.code_hash, second, rig.clock.now(), rig.event())

    assert caught.value.reason is EnrolRefusal.USED
    assert await rig.registry.node(second.device_id) is None
    assert await rig.kinds() == []


async def test_two_devices_presenting_one_invite_at_once_admit_exactly_one(rig: _Rig) -> None:
    invite = await rig.issue()
    nodes = [make_swarm_node(make_device_party(rig.clock), rig.clock) for _ in range(6)]

    results = await asyncio.gather(
        *(rig.registry.redeem(invite.code_hash, n, rig.clock.now(), rig.event()) for n in nodes),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, SwarmNode)]
    losers = [r for r in results if isinstance(r, EnrolmentDeniedError)]
    assert len(winners) == 1 and len(losers) == 5
    assert {e.reason for e in losers} == {EnrolRefusal.USED}
    assert len(await rig.registry.nodes()) == 1 and len(await rig.kinds()) == 1


async def test_an_unknown_token_is_refused(rig: _Rig) -> None:
    node = make_swarm_node(make_device_party(rig.clock), rig.clock)

    with pytest.raises(EnrolmentDeniedError) as caught:
        await rig.registry.redeem(token_hash(new_invite_token()), node, rig.clock.now())

    assert caught.value.reason is EnrolRefusal.UNKNOWN_INVITE


async def test_a_lapsed_invite_is_refused_and_the_refusal_still_moves_it_to_expired(
    rig: _Rig,
) -> None:
    invite = await rig.issue()
    node = make_swarm_node(make_device_party(rig.clock), rig.clock)

    with pytest.raises(EnrolmentDeniedError) as caught:
        await rig.registry.redeem(invite.code_hash, node, invite.expires_at)

    stored = await rig.registry.get_invite(invite.code_hash)
    assert caught.value.reason is EnrolRefusal.EXPIRED
    assert stored is not None and stored.state is InviteState.EXPIRED
    assert await rig.registry.node(node.device_id) is None


async def test_a_revoked_invite_is_refused(rig: _Rig) -> None:
    invite = await rig.issue()
    await rig.registry.revoke_invite(invite.code_hash)
    node = make_swarm_node(make_device_party(rig.clock), rig.clock)

    with pytest.raises(EnrolmentDeniedError) as caught:
        await rig.registry.redeem(invite.code_hash, node, rig.clock.now())

    assert caught.value.reason is EnrolRefusal.REVOKED


@pytest.mark.parametrize(
    "shared", ["device_id", "node_id", "cell_id", "warden_id", "public_key_hex"]
)
async def test_a_node_sharing_any_identity_with_an_enrolled_one_is_refused_and_spends_nothing(
    rig: _Rig, shared: str
) -> None:
    enrolled = await rig.enrol()
    invite = await rig.issue()
    other = make_device_party(rig.clock)
    twin = make_swarm_node(other, rig.clock, **{shared: getattr(enrolled, shared)})

    with pytest.raises(EnrolmentDeniedError) as caught:
        await rig.registry.redeem(invite.code_hash, twin, rig.clock.now(), rig.event())

    unspent = await rig.registry.get_invite(invite.code_hash)
    assert caught.value.reason is EnrolRefusal.IDENTITY_IN_USE
    assert unspent is not None and unspent.state is InviteState.ISSUED
    assert len(await rig.registry.nodes()) == 1 and await rig.kinds() == []


# ──────────────────────────────────────────────────────────────────────────────
# Nodes: lookups and listing
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_node_is_found_by_device_node_id_and_cell(rig: _Rig) -> None:
    node = await rig.enrol()

    assert await rig.registry.node(node.device_id) == node
    assert await rig.registry.node_by_node_id(node.node_id) == node
    assert await rig.registry.node_by_cell(node.cell_id) == node


async def test_a_stranger_is_found_by_none_of_them(rig: _Rig) -> None:
    await rig.enrol()

    assert await rig.registry.node(new_device_id(rig.clock)) is None
    assert await rig.registry.node_by_node_id(new_node_id(rig.clock)) is None
    assert await rig.registry.node_by_cell(new_cell_id(rig.clock)) is None


async def test_a_stored_node_comes_back_exactly_as_it_went_in(rig: _Rig) -> None:
    node = await rig.enrol(
        label="kitchen-pi",
        capabilities=make_capabilities(),
        capacity=make_capacity(),
        max_sub_bees=3,
        last_seen_at=None,
    )

    assert await rig.registry.node(node.device_id) == node


async def test_nodes_are_listed_oldest_enrolment_first_and_filtered_by_status(rig: _Rig) -> None:
    first = await rig.enrol()
    rig.clock.advance(1.0)
    second = await rig.enrol()
    await rig.registry.set_status(
        first.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now()
    )

    assert [n.device_id for n in await rig.registry.nodes()] == [first.device_id, second.device_id]
    online = await rig.registry.nodes(NodeStatus.ONLINE)
    assert [n.device_id for n in online] == [second.device_id]
    assert await rig.registry.nodes(NodeStatus.REVOKED) == ()


# ──────────────────────────────────────────────────────────────────────────────
# Nodes: status, a compare-and-set along one machine
# ──────────────────────────────────────────────────────────────────────────────


async def test_set_status_moves_a_node_and_records_the_event_with_it(rig: _Rig) -> None:
    node = await rig.enrol()
    event = rig.event("swarm.unreachable", node.device_id)

    moved = await rig.registry.set_status(
        node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now(), event
    )

    assert moved.status is NodeStatus.UNREACHABLE and moved.revoked_at is None
    assert await rig.registry.node(node.device_id) == moved
    assert await rig.trail.query(TrailQuery()) == (event,)


async def test_set_status_refuses_a_stale_expectation_and_records_nothing(rig: _Rig) -> None:
    node = await rig.enrol()

    with pytest.raises(NodeStatusConflictError):
        await rig.registry.set_status(
            node.device_id, NodeStatus.UNREACHABLE, NodeStatus.ONLINE, rig.clock.now(), rig.event()
        )

    stored = await rig.registry.node(node.device_id)
    assert stored is not None and stored.status is NodeStatus.ONLINE and await rig.kinds() == []


async def test_set_status_walks_the_machine_and_refuses_a_forbidden_edge(rig: _Rig) -> None:
    node = await rig.enrol()
    now = rig.clock.now()
    await rig.registry.set_status(node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, now)
    await rig.registry.set_status(node.device_id, NodeStatus.UNREACHABLE, NodeStatus.ONLINE, now)

    with pytest.raises(InvalidNodeTransitionError):
        await rig.registry.set_status(node.device_id, NodeStatus.ONLINE, NodeStatus.ONLINE, now)


async def test_revoking_stamps_the_time_and_is_final(rig: _Rig) -> None:
    node = await rig.enrol()
    at = rig.clock.now() + timedelta(seconds=30)

    revoked = await rig.registry.set_status(
        node.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, at
    )

    assert revoked.status is NodeStatus.REVOKED and revoked.revoked_at == at
    for new in (NodeStatus.ONLINE, NodeStatus.UNREACHABLE):
        with pytest.raises(InvalidNodeTransitionError):
            await rig.registry.set_status(node.device_id, NodeStatus.REVOKED, new, at)


_Change = Callable[[SwarmRegistry, SwarmNode, FakeClock], Awaitable[object]]
_CHANGES: dict[str, _Change] = {
    "set_level": lambda r, n, c: r.set_level(n.device_id, RuntimeLevel.COLONIZED),
    "set_desired_level": lambda r, n, c: r.set_desired_level(n.device_id, RuntimeLevel.COLONIZED),
    "set_access": lambda r, n, c: r.set_access(n.device_id, AccessLevel.READ_ONLY, ("fs:read:**",)),
    "set_shield": lambda r, n, c: r.set_shield(n.device_id, CombShieldLevel.PROPOLIS),
    "touch": lambda r, n, c: r.touch(n.device_id, c.now()),
    "update_facts": lambda r, n, c: r.update_facts(n.device_id, n.capabilities, n.capacity),
}


@pytest.mark.parametrize("change", list(_CHANGES))
async def test_a_revoked_node_accepts_no_change_at_all(rig: _Rig, change: str) -> None:
    node = await rig.enrol()
    await rig.registry.set_status(
        node.device_id, NodeStatus.ONLINE, NodeStatus.REVOKED, rig.clock.now()
    )
    revoked = await rig.registry.node(node.device_id)

    with pytest.raises(NodeRevokedError):
        await _CHANGES[change](rig.registry, node, rig.clock)

    assert await rig.registry.node(node.device_id) == revoked


@pytest.mark.parametrize("change", list(_CHANGES))
async def test_a_change_to_a_device_that_is_not_enrolled_raises_not_found(
    rig: _Rig, change: str
) -> None:
    stranger = make_swarm_node(make_device_party(rig.clock), rig.clock)

    with pytest.raises(NodeNotFoundError):
        await _CHANGES[change](rig.registry, stranger, rig.clock)


async def test_set_status_on_a_device_that_is_not_enrolled_raises_not_found(rig: _Rig) -> None:
    with pytest.raises(NodeNotFoundError):
        await rig.registry.set_status(
            new_device_id(rig.clock), NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now()
        )


# ──────────────────────────────────────────────────────────────────────────────
# Nodes: the ladder, access, tier, liveness and reported facts
# ──────────────────────────────────────────────────────────────────────────────


async def test_set_level_moves_one_rung_at_a_time_and_records_the_event(rig: _Rig) -> None:
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


async def test_a_rung_may_not_be_skipped(rig: _Rig) -> None:
    node = await rig.enrol()

    with pytest.raises(LadderError):
        await rig.registry.set_level(node.device_id, RuntimeLevel.NUC)

    stored = await rig.registry.node(node.device_id)
    assert stored is not None and stored.level is RuntimeLevel.GATEWAY


async def test_reaching_the_requested_rung_clears_the_request_and_passing_another_keeps_it(
    rig: _Rig,
) -> None:
    node = await rig.enrol()
    asked = await rig.registry.set_desired_level(node.device_id, RuntimeLevel.NUC)
    assert asked.desired_level is RuntimeLevel.NUC

    midway = await rig.registry.set_level(node.device_id, RuntimeLevel.COLONIZED)
    arrived = await rig.registry.set_level(node.device_id, RuntimeLevel.NUC)

    assert midway.desired_level is RuntimeLevel.NUC
    assert arrived.desired_level is None


async def test_asking_for_the_rung_the_node_is_on_asks_for_nothing_and_none_withdraws(
    rig: _Rig,
) -> None:
    node = await rig.enrol()

    same = await rig.registry.set_desired_level(node.device_id, RuntimeLevel.GATEWAY)
    asked = await rig.registry.set_desired_level(node.device_id, RuntimeLevel.COLONIZED)
    withdrawn = await rig.registry.set_desired_level(node.device_id, None)

    assert same.desired_level is None
    assert asked.desired_level is RuntimeLevel.COLONIZED
    assert withdrawn.desired_level is None


async def test_set_access_replaces_the_level_and_the_capability_strings_together(
    rig: _Rig,
) -> None:
    node = await rig.enrol()
    event = rig.event("swarm.access_changed", node.device_id)

    changed = await rig.registry.set_access(
        node.device_id, AccessLevel.READ_ONLY, ("fs:read:**",), event
    )

    assert changed.access_level is AccessLevel.READ_ONLY
    assert changed.granted_capabilities == ("fs:read:**",)
    assert await rig.registry.node(node.device_id) == changed
    assert await rig.trail.query(TrailQuery()) == (event,)


async def test_set_shield_changes_the_tier_and_never_accepts_night_veil(rig: _Rig) -> None:
    node = await rig.enrol()

    changed = await rig.registry.set_shield(node.device_id, CombShieldLevel.PROPOLIS)
    with pytest.raises(ValidationError):
        await rig.registry.set_shield(node.device_id, CombShieldLevel.NIGHT_VEIL)

    assert changed.comb_shield is CombShieldLevel.PROPOLIS
    stored = await rig.registry.node(node.device_id)
    assert stored is not None and stored.comb_shield is CombShieldLevel.PROPOLIS


async def test_touch_moves_last_seen_forward_only_and_may_update_the_version(rig: _Rig) -> None:
    node = await rig.enrol(last_seen_at=None)
    later = rig.clock.now() + timedelta(seconds=30)

    await rig.registry.touch(node.device_id, later, "1.2.3")
    await rig.registry.touch(node.device_id, later - timedelta(seconds=20))  # A late, older frame.

    stored = await rig.registry.node(node.device_id)
    assert stored is not None
    assert stored.last_seen_at == later and stored.runtime_version == "1.2.3"


async def test_update_facts_replaces_the_capabilities_and_capacity(rig: _Rig) -> None:
    node = await rig.enrol()
    capacity = node.capacity.model_copy(update={"max_sub_bees": 9})

    updated = await rig.registry.update_facts(node.device_id, node.capabilities, capacity)

    assert updated.capacity.max_sub_bees == 9
    assert await rig.registry.node(node.device_id) == updated
