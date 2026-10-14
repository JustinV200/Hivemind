"""Tests for hivemind.queen.swarm_gate.wardens: the table of Level 0 Wardens, one per device.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/wardens.py (codingrules section 3). The Wardens are the
    real class over a `FakeCellSource`, built by `builders.swarm.gate.RecordingFactory` the way the
    composition root builds them over a `SwarmSource`; the Queen they attach to is the real one.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.wardens for the module under test.
"""

from __future__ import annotations

from builders.quarantine import settle
from builders.queen import with_guard_policy
from builders.swarm import GateRig, HubLog

from hivemind.cell import Cell, CellError, CellSession, LeaseRequest, RealCellLease, RealCellSource
from hivemind.cell.lease_state import LeaseState
from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen import Queen
from hivemind.swarm import GatewayUnavailableError
from hivemind.wardens import Warden, WardenState


async def test_serve_attaches_starts_and_runs_the_nodes_warden(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()

        served_now = await rig.wardens.serve(node)

        assert served_now is True
        served = rig.wardens.get(node.device_id)
        assert served is not None and not served.has_ended
        assert served.node == node
        assert served.warden.state is WardenState.ACTIVE  # It leased the device when it started.
        assert [link.warden_id for link in rig.queen.wardens] == [node.warden_id]
        assert rig.wardens.device_ids() == (node.device_id,)
        [spawned] = await rig.queen_deps.trail.query(TrailQuery(kind="warden.spawned"))
        assert spawned.subject_id == node.warden_id


async def test_a_node_already_served_is_not_served_twice(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.wardens.serve(node)
        first = rig.wardens.get(node.device_id)

        again = await rig.wardens.serve(node)

        assert again is False
        assert rig.wardens.get(node.device_id) is first  # A device that is back keeps its Warden.
        assert len(rig.factory.built) == 1
        assert len(rig.queen.wardens) == 1


async def test_serve_does_nothing_before_the_table_is_running(rig: GateRig) -> None:
    node = await rig.enrol()

    assert await rig.wardens.serve(node) is False  # No Queen bound, no group opened.

    rig.wardens.bind(rig.queen)
    assert await rig.wardens.serve(node) is False  # A Queen, but still no group to run it in.
    assert rig.factory.built == []
    assert rig.wardens.device_ids() == ()


async def test_a_warden_the_guard_refuses_leaves_the_device_without_one(rig: GateRig) -> None:
    # A policy whose `queen` role lacks `warden:spawn`: the Guard refuses to admit the Warden.
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("forage:request",))})
    refusing = Queen(with_guard_policy(rig.queen_deps, load_guard_policy(None, section)))
    log = HubLog()
    rig.factory.listeners = (log,)
    node = await rig.enrol()

    async with rig.serving(refusing):
        assert await rig.wardens.serve(node) is False

    assert refusing.wardens == ()
    assert rig.wardens.get(node.device_id) is None
    assert not rig.factory.built[0].link.transport.is_connected  # Its link was closed.
    assert log not in rig.hive.hub._listeners  # And nothing was subscribed for it.
    [denial] = await rig.queen_deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["capability"] == "warden:spawn"


async def test_stop_releases_the_lease_detaches_the_warden_and_closes_its_link(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.wardens.serve(node)
        served = rig.wardens.get(node.device_id)
        assert served is not None
        lease = served.warden.lease
        assert lease is not None and _state_of(lease) is LeaseState.OPEN

        stopped = await rig.wardens.stop(node.device_id)

        assert stopped is True
        assert served.warden.state is WardenState.STOPPED
        assert _state_of(lease) is LeaseState.RELEASED  # The device is left as found.
        assert served.has_ended  # Its run loop is over, awaited.
        assert rig.queen.wardens == ()
        assert not served.built.link.transport.is_connected
        assert rig.wardens.get(node.device_id) is None
        assert await rig.wardens.stop(node.device_id) is False  # Nothing left to stop.


async def test_stop_all_stops_every_warden(rig: GateRig) -> None:
    async with rig.serving():
        nodes = [await rig.enrol() for _ in range(3)]
        for node in nodes:
            await rig.wardens.serve(node)
        wardens = [rig.wardens.get(node.device_id).warden for node in nodes]  # type: ignore[union-attr]

        await rig.wardens.stop_all()

        assert rig.wardens.device_ids() == ()
        assert rig.queen.wardens == ()
        assert [warden.state for warden in wardens] == [WardenState.STOPPED] * 3


async def test_a_lease_the_device_cannot_confirm_released_is_orphaned_not_waited_on(
    rig: GateRig,
) -> None:
    rig.factory.source_for = _StuckSource
    async with rig.serving():
        node = await rig.enrol()
        await rig.wardens.serve(node)
        served = rig.wardens.get(node.device_id)
        assert served is not None
        lease = served.warden.lease
        assert lease is not None

        await rig.wardens.stop(node.device_id)  # Must not raise.

        assert _state_of(lease) is LeaseState.ORPHANED  # The dead-man switch settles it.
        assert rig.queen.wardens == ()
        assert rig.wardens.get(node.device_id) is None


async def test_a_built_wardens_hub_listeners_live_exactly_as_long_as_it_does(
    rig: GateRig,
) -> None:
    log = HubLog()
    rig.factory.listeners = (log,)
    async with rig.serving():
        node = await rig.enrol()
        await rig.wardens.serve(node)
        assert log in rig.hive.hub._listeners  # Subscribed before the Warden leases anything.

        await rig.wardens.stop(node.device_id)

        assert log not in rig.hive.hub._listeners


async def test_a_wardens_run_loop_ending_on_a_device_error_does_not_end_the_table(
    rig: GateRig,
) -> None:
    rig.factory.warden_class = _DyingWarden
    async with rig.serving():
        node = await rig.enrol()
        await rig.wardens.serve(node)
        served = rig.wardens.get(node.device_id)
        assert served is not None

        await settle(lambda: served.has_ended)

        # Still in the table, for the gate to replace; nothing was raised out of the task group.
        assert rig.wardens.get(node.device_id) is served
        assert await rig.wardens.stop(node.device_id) is True


def _state_of(lease: RealCellLease) -> LeaseState:
    """Read a lease's state through a call, so mypy keeps no stale narrowing of it."""
    return lease.state


class _StuckSource:
    """A source whose leases cannot be released: the device is away, so nothing can confirm it."""

    def __init__(self, inner: RealCellSource) -> None:
        self._inner = inner
        self.name = inner.name

    async def cells(self) -> tuple[Cell, ...]:
        return await self._inner.cells()

    async def open_session(self, lease: RealCellLease) -> CellSession:
        return await self._inner.open_session(lease)

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        lease = await self._inner.lease(request)
        lease._releaser = _AwayReleaser()  # type: ignore[assignment]
        return lease


class _AwayReleaser:
    async def release(self, lease: RealCellLease) -> object:
        raise CellError("The device is away, so the release cannot be confirmed.")


class _DyingWarden(Warden):
    """A Warden whose first tick fails the way a device going away mid-command does."""

    async def _tick(self) -> None:
        raise GatewayUnavailableError("The device went away mid-command.")
