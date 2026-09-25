"""Tests for hivemind.cli.readback.virtual_offline: destroying a Virtual Cell from outside a Queen.

`destroy_virtual_cell` is what `hive cells destroy` and `hive cells abscond` share. Run from a
fresh process over the Hive's stores, as either command runs: a Night Veil Cell (known by its tier
label, or by the trail's skeleton alone once no backend lists it) is destroyed behind its boundary,
so its grant's revocation reaches only its segment and the durable trail keeps its skeleton, then
purged; a MEADOW Cell is destroyed exactly as before, its revocation on the trail.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/virtual_offline.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.virtual_offline for the module under test.
    - tests.unit.cli.test_virtual_abscond for the same destroy inside a whole Absconding.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from builders.forage import make_grant
from builders.night_veil import night_veil_manifest, tier_spec

from hivemind.cell import CellIdentity, CombShieldLevel
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.cli.readback.virtual_offline import OfflineCellDeps, destroy_virtual_cell
from hivemind.forage.grant_state import GrantState
from hivemind.manifest import HiveManifest
from hivemind.pheromone import CellEvent, MemoryPheromoneTrail, TrailQuery
from hivemind.queen import ForageLedger
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_event_id

_NIGHT_VEIL_END = ["cell.destroyed", "cell.purged"]  # All a Night Veil Cell's end leaves.


@dataclass(frozen=True, slots=True)
class _Process:
    """A fresh `hive cells` process: its manifest, durable trail, Virtual side, ledger and deps."""

    manifest: HiveManifest
    durable: MemoryPheromoneTrail
    parts: VirtualCellsParts
    ledger: ForageLedger
    offline: OfflineCellDeps

    async def destroy(self, cell_id: CellId, labels: Mapping[str, str]) -> str:
        """Destroy `cell_id` as `hive cells destroy` does, with the labels its backend listed."""
        backend = self.parts.registry.get("fake")
        return await destroy_virtual_cell(backend, self.offline, self.ledger, cell_id, labels)

    async def kinds_about(self, subject_id: str) -> list[str]:
        """The kind of every durable event about `subject_id`, oldest first."""
        return [e.kind for e in await self.durable.query(TrailQuery(subject_id=subject_id))]

    async def kinds(self, kind: str) -> int:
        """How many durable events of `kind` there are."""
        return len(await self.durable.query(TrailQuery(kind=kind)))


def _process(tmp_path: Path) -> _Process:
    """Build what one offline command process builds: a boundary holding nothing yet."""
    manifest = night_veil_manifest(tmp_path)
    clock = FakeClock()
    durable = MemoryPheromoneTrail(clock)
    parts = build_virtual_cells(manifest, durable, clock)
    assert parts is not None
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    offline = OfflineCellDeps(
        trail=durable,
        clock=clock,
        identity=identity,
        leavings=InMemoryLeavingsStore(durable),
        night_veil=parts.night_veil,
    )
    return _Process(manifest, durable, parts, ForageLedger(), offline)


async def _cell_with_a_grant(
    process: _Process, tier: CombShieldLevel
) -> tuple[CellId, Mapping[str, str]]:
    """Provision a Cell at `tier` holding one live grant; return its id and its backend labels."""
    backend = process.parts.registry.get("fake")
    cell = await backend.provision(tier_spec(process.manifest, tier))
    grant = make_grant(GrantState.ACTIVE, process.offline.clock, cell_id=cell.id)
    await process.ledger.record_grant(grant)
    [record] = [
        r for r in await backend.list_cells(process.manifest.hive.id) if r.cell_id == cell.id
    ]
    return cell.id, record.labels


async def test_a_night_veil_cell_is_destroyed_behind_its_boundary_and_purged(
    tmp_path: Path,
) -> None:
    process = _process(tmp_path)
    cell_id, labels = await _cell_with_a_grant(process, CombShieldLevel.NIGHT_VEIL)

    event_id = await process.destroy(cell_id, labels)

    # Its grant's revocation reached only its segment, which the purge took with it.
    assert await process.kinds("forage.revoked") == 0
    assert process.ledger.live_grants() == ()
    assert await process.kinds_about(cell_id) == _NIGHT_VEIL_END
    [destroyed] = await process.durable.query(TrailQuery(kind="cell.destroyed"))
    assert destroyed.id == event_id  # The receipt `hive cells destroy` prints is its skeleton.
    assert process.parts.night_veil.segments.held_cells() == ()
    assert await process.parts.registry.get("fake").list_cells(process.manifest.hive.id) == ()


async def test_a_night_veil_cell_no_backend_lists_is_known_by_its_skeleton(
    tmp_path: Path,
) -> None:
    process = _process(tmp_path)
    gone = new_cell_id(process.offline.clock)
    # The trail's skeleton is all that is left of it: its provisioning named its tier.
    identity = process.offline.identity
    await process.durable.record(
        CellEvent(
            id=new_event_id(process.offline.clock),
            hive_id=identity.hive_id,
            node_id=identity.node_id,
            at=process.offline.clock.now(),
            actor="system",
            kind="cell.provisioned",
            subject_id=gone,
            payload={"comb_shield": CombShieldLevel.NIGHT_VEIL.value},
        )
    )

    await process.destroy(gone, {})

    assert await process.kinds_about(gone) == ["cell.provisioned", *_NIGHT_VEIL_END]


async def test_a_meadow_cell_is_destroyed_as_it_always_was(tmp_path: Path) -> None:
    process = _process(tmp_path)
    cell_id, labels = await _cell_with_a_grant(process, CombShieldLevel.MEADOW)

    await process.destroy(cell_id, labels)

    assert await process.kinds("forage.revoked") == 1
    assert await process.kinds_about(cell_id) == ["cell.destroyed"]
    assert not process.parts.night_veil.segments.owns(cell_id)
