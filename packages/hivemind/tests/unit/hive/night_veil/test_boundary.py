"""Tests for hivemind.hive.night_veil.boundary: a Night Veil Cell's segment across its lifecycle.

Runs a real `CellLifecycle` over a `FakeCellBackend`, recording through the boundary's own
`VeiledTrail`, the way the composition root wires it: a NIGHT_VEIL Cell's records wait in its
segment while it lives and only its skeleton reaches the trail; its teardown purges it; a Queen
restart holds again one that outlived it and purges one gone unpurged. A MEADOW Cell's records
reach the trail exactly as before.

Fits into the Hive:
    Mirrors src/hivemind/hive/night_veil/boundary.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.night_veil.boundary for the module under test.
"""

from __future__ import annotations

from builders.cells import make_identity
from builders.forage import make_capacity
from builders.night_veil import make_night_veil

from hivemind.cell import CellIdentity, CombShieldLevel
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from hivemind.hive.night_veil import (
    TIER_LABEL,
    NightVeilBoundary,
    adopt_night_veil,
    end_night_veil,
    night_veil_cells,
    sweep_night_veil,
    tier_from_labels,
)
from hivemind.hive.overwinter.policy import ReleaseOutcome
from hivemind.hive.registry import BackendRegistry
from hivemind.pheromone import CellEvent, MemoryPheromoneTrail, PheromoneEvent, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import CellId, HiveId, new_cell_id, new_event_id, new_grant_id

_RELEASED = ReleaseOutcome(
    rolled_back_whole_cell=False, has_block_wax=False, single_use=False, backend_can_pause=True
)

_SKELETON_OF_A_TORN_DOWN_CELL = ["cell.provisioned", "cell.destroyed", "cell.purged"]


class _Hive:
    """A lifecycle over one fake backend, recording through a fresh Night Veil boundary.

    Handing it an earlier Hive's backend, trail and identity stands in for a Queen restart: the
    stores outlive the process, and nothing of the earlier Queen's own memory does.
    """

    def __init__(self, earlier: _Hive | None = None) -> None:
        self.clock = FakeClock()
        self.backend: FakeCellBackend = earlier.backend if earlier else FakeCellBackend(self.clock)
        self.durable: MemoryPheromoneTrail = (
            earlier.durable if earlier else MemoryPheromoneTrail(self.clock)
        )
        self.identity: CellIdentity = earlier.identity if earlier else make_identity(self.clock)
        self.night_veil: NightVeilBoundary = make_night_veil(
            self.durable, self.clock, self.identity
        )
        registry = BackendRegistry()
        registry.register("fake", lambda: self.backend)
        self.lifecycle = CellLifecycle(registry, self.night_veil.veiled, self.clock, self.identity)
        self.lifecycle.attach_night_veil(self.night_veil)

    async def durable_about(self, cell_id: str) -> list[PheromoneEvent]:
        return list(await self.durable.query(TrailQuery(subject_id=cell_id)))


def _spec(tier: CombShieldLevel, hive_id: HiveId) -> VirtualCellSpec:
    """A valid spec at `tier` for `hive_id`; NIGHT_VEIL needs the VPN_TOR policy with it."""
    night_veil = tier is CombShieldLevel.NIGHT_VEIL
    return VirtualCellSpec(
        image="night-veil-ubuntu" if night_veil else "base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        network_policy=NetworkPolicy.VPN_TOR if night_veil else NetworkPolicy.EGRESS_ONLY,
        capacity=make_capacity(),
        hive_id=hive_id,
        comb_shield=tier,
    )


async def _live_cycle(hive: _Hive, tier: CombShieldLevel) -> CellId:
    """Provision a Cell at `tier`, mark it ready and grant it; return its id."""
    cell = await hive.lifecycle.provision(_spec(tier, hive.identity.hive_id), "fake")
    await hive.lifecycle.mark_ready(cell.id)
    await hive.lifecycle.grant(cell.id, new_grant_id(hive.clock))
    return cell.id


async def test_a_night_veil_cells_records_wait_in_its_segment_while_it_lives() -> None:
    hive = _Hive()

    cell_id = await _live_cycle(hive, CombShieldLevel.NIGHT_VEIL)

    [provisioned] = await hive.durable_about(cell_id)
    assert provisioned.kind == "cell.provisioned"
    assert provisioned.payload == {
        "backend": "fake",
        "image": "night-veil-ubuntu",
        "comb_shield": "NIGHT_VEIL",
    }
    held = await hive.night_veil.segments.query(cell_id, TrailQuery())
    assert [e.kind for e in held] == [
        "cell.provisioning",
        "cell.provisioned",
        "cell.ready",
        "cell.granted",
    ]


async def test_a_night_veil_cells_teardown_purges_it_down_to_the_skeleton() -> None:
    hive = _Hive()
    cell_id = await _live_cycle(hive, CombShieldLevel.NIGHT_VEIL)
    await hive.lifecycle.release(cell_id, _RELEASED)

    await hive.lifecycle.teardown(cell_id)

    assert [e.kind for e in await hive.durable_about(cell_id)] == _SKELETON_OF_A_TORN_DOWN_CELL
    [purged] = await hive.durable.query(TrailQuery(kind="cell.purged"))
    assert purged.payload == {"events_purged": 7, "side_channel_records_purged": 0}
    assert hive.night_veil.segments.held_cells() == ()


async def test_a_failed_provisions_teardown_purges_the_cell_it_had_created() -> None:
    hive = _Hive()
    spec = _spec(CombShieldLevel.NIGHT_VEIL, hive.identity.hive_id)
    cell = await hive.lifecycle.provision(spec, "fake")

    # PROVISIONING -> DESTROYING: what the provider does when the Cell never proves ready.
    await hive.lifecycle.teardown(cell.id)

    assert [e.kind for e in await hive.durable_about(cell.id)] == _SKELETON_OF_A_TORN_DOWN_CELL
    assert hive.night_veil.segments.held_cells() == ()


async def test_a_meadow_cells_records_reach_the_trail_exactly_as_before() -> None:
    hive = _Hive()
    cell_id = await _live_cycle(hive, CombShieldLevel.MEADOW)

    await hive.lifecycle.teardown(cell_id)

    assert [e.kind for e in await hive.durable_about(cell_id)] == [
        "cell.provisioning",
        "cell.provisioned",
        "cell.ready",
        "cell.granted",
        "cell.destroying",
        "cell.destroyed",
    ]
    assert await hive.durable.query(TrailQuery(kind="cell.purged")) == ()


async def test_every_provisioned_cell_carries_its_tier_in_its_labels() -> None:
    hive = _Hive()

    cell_id = await _live_cycle(hive, CombShieldLevel.NIGHT_VEIL)

    [record] = await hive.backend.list_cells(hive.identity.hive_id)
    assert record.cell_id == cell_id
    assert tier_from_labels(record.labels) is CombShieldLevel.NIGHT_VEIL


def test_tier_from_labels_reads_either_label_and_defaults_to_meadow() -> None:
    assert tier_from_labels({TIER_LABEL: "NIGHT_VEIL"}) is CombShieldLevel.NIGHT_VEIL
    assert tier_from_labels({"hivemind.comb_shield": "PROPOLIS"}) is CombShieldLevel.PROPOLIS
    assert tier_from_labels({TIER_LABEL: "not-a-tier"}) is CombShieldLevel.MEADOW
    assert tier_from_labels({}) is CombShieldLevel.MEADOW


async def test_a_restart_holds_a_living_night_veil_cell_and_purges_a_gone_one() -> None:
    first = _Hive()
    alive = await _live_cycle(first, CombShieldLevel.NIGHT_VEIL)
    gone = await _live_cycle(first, CombShieldLevel.NIGHT_VEIL)
    await first.backend.destroy(gone)  # Lost with its host: no teardown, no record of it.

    # A fresh Queen over the same trail and backend: nothing of the first one's memory survives.
    restarted = _Hive(first)
    await restarted.lifecycle.reconcile(first.identity.hive_id)

    assert restarted.night_veil.segments.held_cells() == (alive,)
    kinds = [e.kind for e in await restarted.durable_about(gone)]
    assert kinds == _SKELETON_OF_A_TORN_DOWN_CELL
    assert await restarted.durable.query(TrailQuery(kind="cell.purged", subject_id=alive)) == ()


async def test_the_restart_sweep_never_purges_a_cell_twice() -> None:
    hive = _Hive()
    cell_id = await _live_cycle(hive, CombShieldLevel.NIGHT_VEIL)
    await hive.lifecycle.teardown(cell_id)

    swept = await sweep_night_veil(hive.night_veil, {})

    assert swept == ()
    assert len(await hive.durable.query(TrailQuery(kind="cell.purged"))) == 1


async def test_end_purges_only_a_night_veil_cell_and_only_once() -> None:
    hive = _Hive()
    meadow, night_veil = new_cell_id(hive.clock), new_cell_id(hive.clock)

    assert await end_night_veil(hive.night_veil, meadow, CombShieldLevel.MEADOW) is None
    assert await end_night_veil(hive.night_veil, night_veil, CombShieldLevel.NIGHT_VEIL)
    assert await end_night_veil(hive.night_veil, night_veil, CombShieldLevel.NIGHT_VEIL) is None
    assert await end_night_veil(None, night_veil, CombShieldLevel.NIGHT_VEIL) is None


async def test_adopt_knows_a_night_veil_cell_by_its_labels_or_its_skeleton() -> None:
    hive = _Hive()
    labelled, attested, meadow = (new_cell_id(hive.clock) for _ in range(3))
    await hive.durable.record(_attested(hive, attested))

    assert await adopt_night_veil(hive.night_veil, labelled, {TIER_LABEL: "NIGHT_VEIL"})
    assert await adopt_night_veil(hive.night_veil, attested, {})
    assert not await adopt_night_veil(hive.night_veil, meadow, {TIER_LABEL: "MEADOW"})
    assert set(hive.night_veil.segments.held_cells()) == {labelled, attested}
    assert await night_veil_cells(hive.night_veil.recorder) == frozenset({attested})


def _attested(hive: _Hive, cell_id: CellId) -> CellEvent:
    """A skeleton `cell.attested` from a Queen older than the tier label: no tier named."""
    return CellEvent(
        id=new_event_id(hive.clock),
        hive_id=hive.identity.hive_id,
        node_id=hive.identity.node_id,
        at=hive.clock.now(),
        actor="system",
        kind="cell.attested",
        subject_id=cell_id,
        payload={"passed": True},
    )
