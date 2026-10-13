"""Tests for hivemind.swarm.shield.shielded: holding a Propolis lease until its tunnel is verified.

Fits into the Hive:
    Mirrors src/hivemind/swarm/shield/shielded.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The decorator wraps a real `SwarmSource` over the scripted device, so a lease
    that is refused is released through the real releaser and the device's side shows it. The
    verifier is a double where the test needs to say exactly how the checks came out, and the real
    `PropolisChecks` where it is the whole path that is shown (the last two tests).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.shield.shielded for the module under test.
    - README.md "Comb Shield level" for the tier.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

import pytest
import structlog.testing
from builders.cells import make_lease_request
from builders.swarm import (
    DeviceScript,
    LinkRig,
    RecordingGuard,
    RouteRow,
    linux_probes,
    make_link_rig,
    make_source,
)

from hivemind.cell import CombShieldLevel, LeaseState
from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.lease import LeaseRequest, RealCellLease
from hivemind.cell.models import Cell
from hivemind.cell.session import CellSession
from hivemind.common.errors import HiveMindError
from hivemind.pheromone import TrailQuery
from hivemind.swarm.errors import GatewayUnavailableError, ShieldUnverifiedError
from hivemind.swarm.shield import CheckResult, PropolisChecks, ShieldedSource, ShieldReport
from hivemind.swarm.shield.shielded import NO_CHECKS_RAN, UNAVAILABLE_CHECK
from hivemind.swarm.source import SwarmSource

_PASS = ShieldReport((CheckResult("default_route_via_tunnel", True, "tunnel"),))
_FAIL = ShieldReport(
    (
        CheckResult("default_route_via_tunnel", False, "direct_default_route"),
        CheckResult("dns_via_tunnel", False, "dns_not_via_tunnel"),
    )
)
_FULL_TUNNEL: list[RouteRow] = [
    ("tun0", "0.0.0.0/1", 0),
    ("tun0", "128.0.0.0/1", 0),
    ("eth0", "0.0.0.0/0", 100),
    ("tun0", "10.8.0.0/24", 0),
]
_LEAKING: list[RouteRow] = [("eth0", "0.0.0.0/0", 100)]


@dataclass
class _Verifier:
    """A `ShieldVerifier` double: answers with a report (or raises) and keeps what it was given."""

    answer: ShieldReport | BaseException
    before: Callable[[], Awaitable[None]] | None = None  # Done as the checks start.
    calls: list[tuple[Cell, RealCellLease]] = field(default_factory=list)
    sessions: list[CellSession] = field(default_factory=list)
    was_open: list[bool] = field(default_factory=list)

    async def verify(self, cell: Cell, lease: RealCellLease, session: CellSession) -> ShieldReport:
        """Keep the arguments, then answer."""
        if self.before is not None:
            await self.before()
        self.calls.append((cell, lease))
        self.sessions.append(session)
        self.was_open.append(session.is_open)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@dataclass
class _Rig:
    link: LinkRig
    source: SwarmSource

    def request(self) -> LeaseRequest:
        """A lease request for the device's Cell, held by its Warden."""
        return make_lease_request(
            self.link.clock, cell_id=self.link.node.cell_id, holder=self.link.node.warden_id
        )


async def _rig(shield: CombShieldLevel, script: DeviceScript | None = None) -> _Rig:
    """A started link to a device of the given tier, and the source that leases it."""
    link = await make_link_rig(
        script or DeviceScript(comb_shield=shield.to_wire()),
        node_overrides={"comb_shield": shield},
    )
    return _Rig(link, make_source(link, RecordingGuard()))


@pytest.fixture
async def rigs() -> AsyncIterator[list[_Rig]]:
    """The rigs a test opened, all closed afterwards."""
    opened: list[_Rig] = []
    yield opened
    for rig in opened:
        await rig.link.close()


async def test_a_meadow_lease_passes_straight_through_and_nothing_is_checked(
    rigs: list[_Rig],
) -> None:
    rig = await _rig(CombShieldLevel.MEADOW)
    rigs.append(rig)
    verifier = _Verifier(_PASS)

    lease = await ShieldedSource(rig.source, verifier).lease(rig.request())

    assert lease.state is LeaseState.OPEN and verifier.calls == []


async def test_a_propolis_lease_is_returned_open_once_the_checks_pass_and_the_session_is_closed(
    rigs: list[_Rig],
) -> None:
    rig = await _rig(CombShieldLevel.PROPOLIS)
    rigs.append(rig)
    verifier = _Verifier(_PASS)

    lease = await ShieldedSource(rig.source, verifier).lease(rig.request())

    ((cell, checked),) = verifier.calls
    assert lease.state is LeaseState.OPEN and checked is lease
    assert cell.id == rig.link.node.cell_id and verifier.was_open == [True]
    assert not verifier.sessions[0].is_open  # The throwaway session is closed again.


async def test_a_propolis_lease_whose_checks_fail_is_released_and_refused_naming_the_checks(
    rigs: list[_Rig],
) -> None:
    rig = await _rig(CombShieldLevel.PROPOLIS)
    rigs.append(rig)
    verifier = _Verifier(_FAIL)

    with pytest.raises(ShieldUnverifiedError) as caught:
        await ShieldedSource(rig.source, verifier).lease(rig.request())

    ((_cell, refused),) = verifier.calls
    assert caught.value.failed_checks == ("default_route_via_tunnel", "dns_via_tunnel")
    assert refused.state is LeaseState.RELEASED
    assert [r.lease_id for r in rig.link.device.state.released] == [refused.id]


@pytest.mark.parametrize(
    ("answer", "named"),
    [
        (ShieldReport(()), NO_CHECKS_RAN),
        (GatewayUnavailableError("the link dropped"), UNAVAILABLE_CHECK),
        (LeaseRefusedError("cell_x", "gone"), UNAVAILABLE_CHECK),
    ],
    ids=["no-checks", "link-lost", "refused"],
)
async def test_anything_that_stops_the_checks_counts_as_unverified(
    rigs: list[_Rig], answer: ShieldReport | HiveMindError, named: str
) -> None:
    rig = await _rig(CombShieldLevel.PROPOLIS)
    rigs.append(rig)

    with pytest.raises(ShieldUnverifiedError) as caught:
        await ShieldedSource(rig.source, _Verifier(answer)).lease(rig.request())

    assert caught.value.failed_checks == (named,)
    assert len(rig.link.device.state.released) == 1


async def test_a_task_cancelled_during_the_checks_leaves_no_lease_open(rigs: list[_Rig]) -> None:
    rig = await _rig(CombShieldLevel.PROPOLIS)
    rigs.append(rig)

    with pytest.raises(asyncio.CancelledError):
        await ShieldedSource(rig.source, _Verifier(asyncio.CancelledError())).lease(rig.request())

    assert len(rig.link.device.state.released) == 1


async def test_a_lease_that_cannot_be_released_is_logged_left_orphaned_and_the_refusal_stands(
    rigs: list[_Rig],
) -> None:
    rig = await _rig(CombShieldLevel.PROPOLIS)
    rigs.append(rig)
    # The device's link is lost while the checks run, so the release cannot reach it.
    verifier = _Verifier(_FAIL, before=rig.link.link.close)

    with structlog.testing.capture_logs() as captured, pytest.raises(ShieldUnverifiedError):
        await ShieldedSource(rig.source, verifier).lease(rig.request())

    [(_cell, stranded)] = verifier.calls
    assert stranded.state is LeaseState.ORPHANED
    assert "swarm.shield_release_failed" in [entry["event"] for entry in captured]


class _EmptyCells:
    """A source that leases like the one it wraps but has stopped listing any Cell."""

    def __init__(self, inner: SwarmSource) -> None:
        self._inner = inner
        self.name = inner.name

    async def cells(self) -> tuple[Cell, ...]:
        """List nothing: the Cell has left the source."""
        return ()

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Lease through the wrapped source."""
        return await self._inner.lease(request)

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Open a session through the wrapped source."""
        return await self._inner.open_session(lease)


async def test_a_cell_that_left_the_source_while_it_was_leased_cannot_be_verified(
    rigs: list[_Rig],
) -> None:
    rig = await _rig(CombShieldLevel.PROPOLIS)
    rigs.append(rig)
    verifier = _Verifier(_PASS)

    with pytest.raises(ShieldUnverifiedError) as caught:
        await ShieldedSource(_EmptyCells(rig.source), verifier).lease(rig.request())

    assert caught.value.failed_checks == (UNAVAILABLE_CHECK,) and verifier.calls == []
    assert len(rig.link.device.state.released) == 1


async def test_everything_else_is_the_wrapped_sources(rigs: list[_Rig]) -> None:
    rig = await _rig(CombShieldLevel.MEADOW)
    rigs.append(rig)
    shielded = ShieldedSource(rig.source, _Verifier(_PASS))

    assert shielded.name == rig.source.name
    assert await shielded.cells() == await rig.source.cells()
    lease = await shielded.lease(rig.request())
    session = await shielded.open_session(lease)
    assert session.is_open


async def test_the_real_checks_over_the_real_session_admit_a_device_with_a_full_tunnel(
    rigs: list[_Rig],
) -> None:
    script = DeviceScript(
        comb_shield=CombShieldLevel.PROPOLIS.to_wire(),
        execs=linux_probes(_FULL_TUNNEL, (), ["10.8.0.1"]),
    )
    rig = await _rig(CombShieldLevel.PROPOLIS, script)
    rigs.append(rig)

    lease = await ShieldedSource(rig.source, PropolisChecks(rig.link.recorder)).lease(rig.request())

    assert lease.state is LeaseState.OPEN
    assert len(await rig.link.trail.query(TrailQuery(kind="swarm.shield_verified"))) == 1


async def test_the_real_checks_refuse_a_device_that_leaks_and_leave_it_as_found(
    rigs: list[_Rig],
) -> None:
    script = DeviceScript(
        comb_shield=CombShieldLevel.PROPOLIS.to_wire(),
        execs=linux_probes(_LEAKING, (), ["8.8.8.8"]),
    )
    rig = await _rig(CombShieldLevel.PROPOLIS, script)
    rigs.append(rig)

    with pytest.raises(ShieldUnverifiedError) as caught:
        await ShieldedSource(rig.source, PropolisChecks(rig.link.recorder)).lease(rig.request())

    assert "default_route_via_tunnel" in caught.value.failed_checks
    assert len(await rig.link.trail.query(TrailQuery(kind="swarm.shield_refused"))) == 1
    assert len(rig.link.device.state.released) == 1
