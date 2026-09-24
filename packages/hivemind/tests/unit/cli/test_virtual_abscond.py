"""Tests for hivemind.cli.readback.virtual_abscond: a separate Absconding and the Night Veil.

A `hive cells abscond` process starts from the Hive's stores alone: the Queen that provisioned the
Cell is gone, so the process holds no segment of it and never saw its grant issued. Its pass still
leaves only a Night Veil Cell's skeleton on the trail: the Cell is known by its tier label, its
grant is filed under it before the Undertaker revokes it, and the purge runs once it is gone. A
MEADOW Cell's Absconding records exactly what it did before.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/virtual_abscond.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.virtual_abscond for the module under test.
    - tests.e2e.test_night_veil_boundary for the same end inside a running Hive's own process.
"""

from __future__ import annotations

from pathlib import Path

from builders.cli import fake_manifest
from builders.forage import make_capacity, make_grant

from hivemind.cell import CombShieldLevel
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.cli.readback.virtual_abscond import AbscondDeps, run_abscond
from hivemind.forage.grant_state import GrantState
from hivemind.hive import NetworkPolicy, VirtualCellSpec
from hivemind.hive.night_veil import with_tier_label
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from hivemind.queen import ForageLedger
from hivemind.queen.cluster import InMemoryOrderStore
from waggle.clock import FakeClock
from waggle.ids import CellId

_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion:8710"  # A valid v3 one.
_CLEANUP_COUNTS = {"grants_revoked": 1, "wax_retired": 0, "leavings_removed": 0}


def _manifest(tmp_path: Path) -> HiveManifest:
    """A `fake_manifest` with the fake Virtual backend and a complete Night Veil tier profile."""
    manifest_path = fake_manifest(tmp_path)
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(
            '\n[virtual_cells]\nbackend = "fake"\n'
            "\n[security.tiers.NIGHT_VEIL]\n"
            'egress_profile = "vpn_tor"\ncontrol_channel = "tor_hidden_service"\n'
            f'hidden_service_address = "{_ONION}"\ntor_socks = "127.0.0.1:9050"\n'
            'locale_profile = "C.UTF-8"\n'
        )
    return load_manifest(manifest_path)


def _spec(manifest: HiveManifest, tier: CombShieldLevel) -> VirtualCellSpec:
    """A spec at `tier`, labelled with it as the lifecycle labels every Cell it provisions."""
    night_veil = tier is CombShieldLevel.NIGHT_VEIL
    return with_tier_label(
        VirtualCellSpec(
            image="night-veil-ubuntu" if night_veil else "base-ubuntu",
            cpu_cores=1,
            memory_bytes=512 * 1024 * 1024,
            disk_bytes=1024 * 1024 * 1024,
            network_policy=NetworkPolicy.VPN_TOR if night_veil else NetworkPolicy.EGRESS_ONLY,
            capacity=make_capacity(),
            hive_id=manifest.hive.id,
            comb_shield=tier,
        )
    )


async def _abscond_one_cell(
    tmp_path: Path, tier: CombShieldLevel
) -> tuple[VirtualCellsParts, CellId, MemoryPheromoneTrail]:
    """Leave one Cell at `tier` with one live grant behind, then run a fresh process's pass."""
    manifest = _manifest(tmp_path)
    clock = FakeClock()
    durable = MemoryPheromoneTrail(clock)
    # Built over the plain trail, as the command builds it: a boundary holding nothing yet.
    parts = build_virtual_cells(manifest, durable, clock)
    assert parts is not None
    cell = await parts.registry.get("fake").provision(_spec(manifest, tier))
    ledger = ForageLedger()
    await ledger.record_grant(make_grant(GrantState.ACTIVE, clock, cell_id=cell.id))

    summary = await run_abscond(
        AbscondDeps(
            manifest=manifest,
            trail=durable,
            ledger=ledger,
            orders=InMemoryOrderStore(),
            clock=clock,
            virtual_cells=parts,
            leavings=InMemoryLeavingsStore(durable),
        )
    )

    assert summary.containers_destroyed == 1
    assert summary.grants_revoked == 1
    assert summary.left_as_found
    return parts, cell.id, durable


async def test_a_separate_absconding_leaves_only_a_night_veil_cells_skeleton(
    tmp_path: Path,
) -> None:
    parts, cell_id, durable = await _abscond_one_cell(tmp_path, CombShieldLevel.NIGHT_VEIL)

    # The grant's revocation stayed in the Cell's segment and went with it; its destruction
    # crossed as its skeleton, and the purge recorded that it ran.
    events = await durable.query(TrailQuery())
    assert [(e.kind, e.subject_id) for e in events] == [
        ("cell.destroyed", cell_id),
        ("cell.purged", cell_id),
    ]
    assert events[0].payload == _CLEANUP_COUNTS
    assert events[1].payload == {"events_purged": 2, "side_channel_records_purged": 0}
    assert parts.night_veil.segments.held_cells() == ()


async def test_a_meadow_cells_absconding_records_what_it_always_did(tmp_path: Path) -> None:
    parts, cell_id, durable = await _abscond_one_cell(tmp_path, CombShieldLevel.MEADOW)

    events = await durable.query(TrailQuery())
    assert [e.kind for e in events] == ["forage.revoked", "cell.destroyed"]
    assert events[1].subject_id == cell_id
    assert events[1].payload == _CLEANUP_COUNTS
    assert not parts.night_veil.segments.owns(cell_id)
