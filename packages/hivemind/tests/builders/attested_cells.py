"""Build a Virtual Cell provider whose fake backend plays its Cells' dial-back, for tests.

`LifecycleVirtualCellProvider.acquire` provisions a Cell, waits for the Cell's own handshake on a
`QueenReadinessGate`, finds the `WardenLink` the Queen's listener attached, attests the Cell at its
tier and only then marks it ready. A `FakeCellBackend` never dials a gate (no real container, no
real listener), so `GatedFakeCellBackend` plays both halves in one `provision`: the backend's own
`expect` and the listener's attach-then-resolve. `make_provider_rig` wires that backend into a real
`CellLifecycle`, a real gate and a real provider over a `CellAttestation` whose probe factories a
test chooses, and returns every part a scenario reads. The Propolis unit tests and the Propolis
end-to-end scenarios both start here, so they state only the fact under test: the second kind
hands the rig a running Queen's own clock and trail (`RigWorld`) and points the backend's `attach`
at her.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests/unit/queen/cell_gate/test_provider_propolis.py and
    tests/e2e/test_virtual_cells_propolis.py.

Key invariants:
    - The rig's provider is the real one: only the backend's dial-back and the probes are stood in.
    - A tier's probe factory left at None is left unconfigured, not defaulted: a test that wants
      the production default of "no probe, no Cell" gets exactly that.
    - A Cell's link announces the tier it was provisioned at unless a test says otherwise.

See Also:
    - tests/unit/queen/cell_gate/test_provider.py for the same shape, private to that module.
    - hivemind.queen.cell_gate.provider for the class under test.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from builders.cells import make_identity
from builders.forage import make_capacity

from hivemind.cell import Cell, CombShieldLevel
from hivemind.cell.source import CellIdentity
from hivemind.hive import NetworkPolicy
from hivemind.hive.attestation import CellAttestation, NightVeilProbeFactory, PropolisProbeFactory
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.bootstrap import CellReadyInfo, QueenEndpoint
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.night_veil import FakeNightVeilProbe
from hivemind.hive.registry import BackendRegistry
from hivemind.pheromone import PheromoneTrail, TrailRecorder
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.gate import QueenReadinessGate
from hivemind.queen.cell_gate.provider import LifecycleVirtualCellProvider
from hivemind.queen.deps import WardenLink
from waggle.clock import Clock, FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import HiveId, new_hive_id, new_node_id, new_warden_id
from waggle.transport.memory import MemoryTransport

__all__ = [
    "GatedFakeCellBackend",
    "ProviderRig",
    "RigWorld",
    "StubQueen",
    "make_provider_rig",
    "make_tier_spec",
]


class GatedFakeCellBackend(FakeCellBackend):
    """A FakeCellBackend that also plays the real backend's and the Queen's listener's roles.

    `provision` mints the Cell as usual, then registers and resolves it on `gate` and attaches a
    fresh `WardenLink` -- what a real backend (`expect`) and a real `CellListener` (attach, then
    `resolve`) do together, collapsed into one step. The link is appended to `wardens`, or handed
    to `attach` when a test has pointed that at a running Queen.
    """

    def __init__(
        self,
        clock: Clock,
        gate: QueenReadinessGate,
        wardens: list[WardenLink],
        announce: CombShieldLevel | None = None,
        endpoint: QueenEndpoint | None = None,
    ) -> None:
        """Build the backend over the `gate` and `wardens` list the provider will read.

        Args:
            clock: The shared clock.
            gate: The readiness gate each provisioned Cell is registered and resolved on.
            wardens: Where each Cell's attached `WardenLink` is appended.
            announce: The tier every link claims; None means the Cell's own tier.
            endpoint: Where the Queen is, as a Cell must see her; when given, a bootstrap is
                minted for every Cell (`bootstraps`), as a real backend's would be.
        """
        super().__init__(
            clock,
            capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None),
            endpoint=endpoint,
        )
        self._queen_gate = gate
        self._wardens = wardens
        self._announce = announce  # The tier each Cell's link claims; its own tier when None.
        self.attach: Callable[[WardenLink], Awaitable[object]] | None = None

    async def provision(self, spec: VirtualCellSpec) -> Cell:
        """Provision a Cell, then dial back for it: register, attach its link and resolve."""
        cell = await super().provision(spec)
        await self._queen_gate.expect(cell.id, "deadbeef")
        announced = (
            cell
            if self._announce is None
            else cell.model_copy(update={"comb_shield": self._announce})
        )
        warden_id = new_warden_id(self._clock)
        link = WardenLink(
            warden_id=warden_id,
            cell=announced,
            transport=MemoryTransport.pair(Codec(), Codec())[0],
            # Addressed from the Hive the spec names, so a running Queen can wrap real envelopes.
            hop=Hop(sender=spec.hive_id, recipient=warden_id, node_id=new_node_id(self._clock)),
        )
        if self.attach is not None:
            await self.attach(link)
        else:
            self._wardens.append(link)
        self._queen_gate.resolve(
            cell.id,
            new_node_id(self._clock),
            CellReadyInfo(capabilities=cell.capabilities, capacity=cell.capacity),
        )
        return cell


class StubQueen:
    """A minimal stand-in for `hivemind.queen.queen.Queen`: only `.wardens` is read."""

    def __init__(self, wardens: list[WardenLink]) -> None:
        """Read `wardens`, the list a backend appends each attached link to."""
        self._wardens = wardens

    @property
    def wardens(self) -> tuple[WardenLink, ...]:
        """Every Warden the backend has attached so far."""
        return tuple(self._wardens)


@dataclass(frozen=True, slots=True)
class RigWorld:
    """The clock, trail and identity a rig records under, when a test already has them.

    A running Queen's own, so the Cell events the lifecycle records share her node id: the trail
    orders ties on the clock by node id, and a frozen test clock ties everything, so a rig with a
    node of its own would sort its events apart from hers instead of among them.
    """

    clock: Clock
    trail: PheromoneTrail
    identity: CellIdentity


@dataclass(frozen=True, slots=True)
class ProviderRig:
    """The parts of one provider under test, for a scenario to read."""

    provider: LifecycleVirtualCellProvider
    lifecycle: CellLifecycle
    wardens: list[WardenLink]
    trail: PheromoneTrail
    backend: GatedFakeCellBackend
    clock: Clock


def make_tier_spec(
    tier: CombShieldLevel = CombShieldLevel.MEADOW, hive_id: HiveId | None = None
) -> VirtualCellSpec:
    """A valid spec for a Cell at `tier`: Night Veil needs its VPN_TOR network profile with it."""
    night_veil = tier is CombShieldLevel.NIGHT_VEIL
    return VirtualCellSpec(
        image="night-veil-ubuntu" if night_veil else "base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.VPN_TOR if night_veil else NetworkPolicy.EGRESS_ONLY,
        capacity=make_capacity(),
        hive_id=hive_id if hive_id is not None else new_hive_id(FakeClock()),
        ready_timeout_s=5.0,
        comb_shield=tier,
    )


def make_provider_rig(
    *,
    propolis: PropolisProbeFactory | None = None,
    night_veil: NightVeilProbeFactory | None = None,
    announce: CombShieldLevel | None = None,
    endpoint: QueenEndpoint | None = None,
    world: RigWorld | None = None,
) -> ProviderRig:
    """Build a real provider over a gated fake backend, with the probe factories given.

    Args:
        propolis: Builds a PROPOLIS Cell's probe; None leaves the tier unconfigured.
        night_veil: Builds a NIGHT_VEIL Cell's probe; None means an all-green fake.
        announce: The tier every Cell's link claims; None means the tier it was provisioned at.
        endpoint: Where the Queen is, as a Cell must see her; None mints no bootstrap.
        world: The clock, trail and identity to record under; None makes a fresh set.
    """
    clock: Clock = world.clock if world is not None else FakeClock()
    trail: PheromoneTrail = world.trail if world is not None else MemoryPheromoneTrail(clock)
    identity = world.identity if world is not None else make_identity(clock)
    registry = BackendRegistry()
    gate = QueenReadinessGate()
    wardens: list[WardenLink] = []
    backend = GatedFakeCellBackend(clock, gate, wardens, announce, endpoint)
    registry.register("fake", lambda: backend)
    lifecycle = CellLifecycle(registry, trail, clock, identity)
    recorder = TrailRecorder(
        trail=trail, clock=clock, hive_id=identity.hive_id, node_id=identity.node_id
    )
    attestation = CellAttestation(
        recorder,
        night_veil if night_veil is not None else (lambda _c: FakeNightVeilProbe()),
        propolis,
    )
    provider = LifecycleVirtualCellProvider(lifecycle, gate, attestation)
    provider.bind_queen(StubQueen(wardens))
    return ProviderRig(provider, lifecycle, wardens, trail, backend, clock)
