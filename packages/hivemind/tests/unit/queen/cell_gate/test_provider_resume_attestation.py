"""Tests for hivemind.queen.cell_gate.provider attesting a Cell again as it leaves Overwinter.

Split by feature from test_provider.py and test_provider_propolis.py (codingrules 5.1). An
attestation is a look at one moment (ADR-0057): a PROPOLIS Cell's tunnel can drop while the Cell
sleeps, so the provider looks again before a resumed Cell's link is handed back. The provider,
lifecycle, Overwinter pool and gate are the real ones; only the backend's dial-back
(`builders.attested_cells.GatedFakeCellBackend`) and the PROPOLIS probe are stood in, the probe
switchable between a pass and a red Tor check so one Cell can pass when provisioned and fail on
its return.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/provider.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.provider for the class under test.
    - docs/adr/0057-propolis-is-verified-by-looking.md for why a resumed Cell is looked at again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from builders.attested_cells import GatedFakeCellBackend, StubQueen, make_tier_spec
from builders.cells import make_identity

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, CombShieldLevel
from hivemind.hive.attestation import CellAttestation
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.errors import CellProvisionError
from hivemind.hive.lifecycle import CellLifecycle, OverwinterSettings
from hivemind.hive.night_veil import CheckResult, CheckStatus, FakeNightVeilProbe
from hivemind.hive.overwinter.policy import OverwinterConfig, OverwinterDecision, ReleaseOutcome
from hivemind.hive.overwinter.pool import OverwinterPool
from hivemind.hive.propolis import CHECK_TOR, FakePropolisProbe, PropolisProbe
from hivemind.hive.registry import BackendRegistry
from hivemind.pheromone import TrailQuery, TrailRecorder
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.provider import LifecycleVirtualCellProvider
from hivemind.queen.deps import WardenLink
from hivemind.queen.placement import ProvisionVirtual, ReuseDormant
from waggle.clock import FakeClock
from waggle.ids import new_grant_id

_RED_TOR = CheckResult(status=CheckStatus.FAIL, detail="a process named tor is running")


@dataclass
class _SwitchableProbes:
    """A PROPOLIS probe factory whose Cells pass until a test flips `tor_running`."""

    tor_running: bool = False
    made: int = field(default=0)

    def __call__(self, _cell: Cell) -> PropolisProbe:
        self.made += 1
        return FakePropolisProbe({CHECK_TOR: _RED_TOR} if self.tor_running else None)


@dataclass(frozen=True, slots=True)
class _Rig:
    """A provider over a lifecycle with an Overwinter pool, and what a test reads back."""

    provider: LifecycleVirtualCellProvider
    lifecycle: CellLifecycle
    wardens: list[WardenLink]
    trail: MemoryPheromoneTrail
    probes: _SwitchableProbes


def _rig() -> _Rig:
    """Build the real provider over a gated fake backend that can pause, with a pool to sleep in."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    identity = make_identity(clock)
    gate = QueenReadinessGate()
    wardens: list[WardenLink] = []
    backend = GatedFakeCellBackend(clock, gate, wardens)
    registry = BackendRegistry()
    registry.register("fake", lambda: backend)
    config = OverwinterConfig(
        enabled=True, max_cells=10, max_per_image=10, max_dormant_s=3600.0, disk_budget_mb=1024**3
    )
    overwinter = OverwinterSettings(pool=OverwinterPool(clock, config), config=config)
    lifecycle = CellLifecycle(registry, trail, clock, identity, overwinter=overwinter)
    recorder = TrailRecorder(
        trail=trail, clock=clock, hive_id=identity.hive_id, node_id=identity.node_id
    )
    probes = _SwitchableProbes()
    attestation = CellAttestation(recorder, lambda _c: FakeNightVeilProbe(), probes)
    provider = LifecycleVirtualCellProvider(lifecycle, gate, attestation)
    provider.bind_queen(StubQueen(wardens))
    return _Rig(provider, lifecycle, wardens, trail, probes)


async def _no_op_scrub(cell: object) -> None:
    """A Scrubber that does nothing: scrubbing is not what these tests are about."""


def _task() -> Task:
    """A Task stand-in; `LifecycleVirtualCellProvider.acquire` never reads it for these tiers."""
    return None  # type: ignore[return-value]


async def _sleeping_propolis_cell(rig: _Rig) -> WardenLink:
    """Provision a PROPOLIS Cell (attested green), use it, and Overwinter it; return its link."""
    placement = ProvisionVirtual(
        spec=make_tier_spec(CombShieldLevel.PROPOLIS), backend="fake", reason="test"
    )
    link = await rig.provider.acquire(placement, _task())
    cell_id = link.cell.id
    await rig.lifecycle.grant(cell_id, new_grant_id(FakeClock()))
    outcome = ReleaseOutcome(
        rolled_back_whole_cell=False, has_block_wax=False, single_use=False, backend_can_pause=True
    )
    assert await rig.lifecycle.release(cell_id, outcome) is OverwinterDecision.OVERWINTER
    await rig.lifecycle.overwinter(cell_id, scrub=_no_op_scrub)
    assert rig.lifecycle.status_of(cell_id) is VirtualCellStatus.DORMANT
    return link


async def test_a_resumed_propolis_cell_is_attested_again_before_its_link_is_returned() -> None:
    rig = _rig()
    asleep = await _sleeping_propolis_cell(rig)

    dormant = ReuseDormant(cell_id=asleep.cell.id, warden_id=asleep.warden_id, reason="test")
    link = await rig.provider.acquire(dormant, _task())

    assert link is asleep
    assert rig.probes.made == 2  # Once when provisioned, once more on its return.
    attested = await rig.trail.query(TrailQuery(kind="cell.attested"))
    assert [event.payload["passed"] for event in attested] == [True, True]


async def test_a_resumed_propolis_cell_that_fails_its_second_look_is_torn_down() -> None:
    rig = _rig()
    asleep = await _sleeping_propolis_cell(rig)
    rig.probes.tor_running = True  # Something started Tor in the Cell while it slept.

    dormant = ReuseDormant(cell_id=asleep.cell.id, warden_id=asleep.warden_id, reason="test")
    with pytest.raises(CellProvisionError, match="PROPOLIS attestation failed: tor_disabled"):
        await rig.provider.acquire(dormant, _task())

    assert rig.lifecycle.status_of(asleep.cell.id) is None  # Torn down, never handed back.
    attested = await rig.trail.query(TrailQuery(kind="cell.attested"))
    assert [event.payload["passed"] for event in attested] == [True, False]
