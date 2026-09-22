"""Tests for hivemind.cli.readback.virtual: `hive cells` inspect/destroy/release/snapshot/etc.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/virtual.py and .virtual_abscond.py (codingrules section 3).
    Drives the typer application through `typer.testing.CliRunner`, against a real
    `builders.cli.fake_manifest` manifest (`[virtual_cells] backend = "fake"` appended by
    `_with_fake_backend` below, since that builder itself is shared test infrastructure this
    dispatch does not own) and a real SQLite file. `hivemind.cli.readback.virtual.
    build_virtual_cells` is monkeypatched to hand back one pre-built, pre-seeded
    `VirtualCellsParts` per test (mirroring `test_llm.py`'s own `build_registry` monkeypatch): a
    `FakeCellBackend` provisioned once through its own real `provision()` has no way to be reached
    from outside the CLI process otherwise, since each real `build_virtual_cells` call would
    otherwise hand back a fresh, empty one.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.virtual and .virtual_abscond for the modules under test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cells import make_cell, make_identity
from builders.cli import fake_manifest
from builders.forage import make_capacity
from typer.testing import CliRunner

from hivemind.cell import CellKind, FakeCellSource
from hivemind.cli.app import app
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.cli.stores import open_cluster_orders, open_trail
from hivemind.hive import VirtualCellSpec
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import CellEvent, WardenEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_hive_id, new_node_id, new_warden_id

runner = CliRunner()


def _db_path(manifest_path: Path) -> Path:
    """Return the SQLite file `builders.cli.fake_manifest` wrote `[hive] db` as."""
    return manifest_path.parent / "data" / "hive.sqlite3"


def _with_fake_backend(manifest_path: Path) -> HiveManifest:
    """Append `[virtual_cells] backend = "fake"` to a `fake_manifest`, and load it back."""
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write('\n[virtual_cells]\nbackend = "fake"\n')
    return load_manifest(manifest_path)


def _spec(hive_id: str) -> VirtualCellSpec:
    """Build a minimal, valid VirtualCellSpec for `backend.provision` in a test."""
    return VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1,
        memory_bytes=512 * 1024 * 1024,
        disk_bytes=1024 * 1024 * 1024,
        capacity=make_capacity(),
        hive_id=hive_id,
    )


def _seed_virtual_cell(manifest: HiveManifest) -> tuple[VirtualCellsParts, str]:
    """Build a real VirtualCellsParts and provision one Cell on its own `fake` backend.

    Returns:
        `(parts, cell_id)`: `parts` is what a monkeypatched `build_virtual_cells` should hand
        back for the rest of the test; `cell_id` is the freshly provisioned Cell's own id.
    """
    trail = MemoryPheromoneTrail(FakeClock())
    parts = build_virtual_cells(manifest, trail, FakeClock())
    assert parts is not None  # [virtual_cells] backend is set (module docstring's own helper).
    backend = parts.registry.get("fake")
    cell = asyncio.run(backend.provision(_spec(manifest.hive.id)))
    return parts, cell.id


def _patch(monkeypatch: pytest.MonkeyPatch, parts: VirtualCellsParts) -> None:
    """Make every `hivemind.cli.readback.virtual` command see `parts` for any manifest/trail."""
    monkeypatch.setattr(
        "hivemind.cli.readback.virtual.build_virtual_cells", lambda *_a, **_k: parts
    )


def _patch_real_cell(monkeypatch: pytest.MonkeyPatch) -> str:
    """Fix the Hive Stand's own Cell id for this test, and return it.

    `HiveStandSource.__init__` mints a fresh random id on every construction, so two separate
    `CliRunner.invoke()` calls (each building their own source) can never agree on one by
    themselves; this patches `hivemind.cli.readback.virtual.build_hive_stand_source` to hand back
    a fixed `FakeCellSource` instead, the same monkeypatch shape `_patch` uses for Virtual Cells.
    """
    clock = FakeClock()
    real_cell = make_cell(kind=CellKind.REAL, clock=clock)
    source = FakeCellSource(
        [real_cell], make_identity(clock=clock), MemoryPheromoneTrail(clock), clock
    )
    monkeypatch.setattr(
        "hivemind.cli.readback.virtual.build_hive_stand_source", lambda *_a, **_k: source
    )
    return real_cell.id


# ──────────────────────────────────────────────────────────────────────────────
# inspect
# ──────────────────────────────────────────────────────────────────────────────


def test_inspect_reports_a_virtual_cell_from_backend_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    manifest = _with_fake_backend(manifest_path)
    parts, cell_id = _seed_virtual_cell(manifest)
    _patch(monkeypatch, parts)

    result = runner.invoke(app, ["cells", "inspect", cell_id, "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert "kind:       VIRTUAL" in result.output
    assert "backend:    fake" in result.output


def test_inspect_reports_the_real_hive_stand_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    real_cell_id = _patch_real_cell(monkeypatch)

    result = runner.invoke(
        app, ["cells", "inspect", real_cell_id, "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 0, result.output
    assert "kind:        REAL" in result.output


def test_inspect_exits_1_on_an_unknown_id(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(
        app, ["cells", "inspect", "no-such-cell", "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 1


# ──────────────────────────────────────────────────────────────────────────────
# destroy
# ──────────────────────────────────────────────────────────────────────────────


def test_destroy_removes_a_virtual_cell_and_prints_the_event_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    manifest = _with_fake_backend(manifest_path)
    parts, cell_id = _seed_virtual_cell(manifest)
    _patch(monkeypatch, parts)

    result = runner.invoke(app, ["cells", "destroy", cell_id, "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert result.output.strip()  # The trail event id, non-empty.
    remaining = asyncio.run(parts.registry.get("fake").list_cells(manifest.hive.id))
    assert remaining == ()


def test_destroy_of_an_unknown_id_is_a_clean_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    manifest = _with_fake_backend(manifest_path)
    parts, _cell_id = _seed_virtual_cell(manifest)
    _patch(monkeypatch, parts)
    unknown_id = new_cell_id(FakeClock())  # Syntactically valid; never provisioned anywhere.

    result = runner.invoke(app, ["cells", "destroy", unknown_id, "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output


def test_destroy_refuses_the_real_hive_stand_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    real_cell_id = _patch_real_cell(monkeypatch)

    result = runner.invoke(
        app, ["cells", "destroy", real_cell_id, "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 1
    assert "Real Cell" in result.output


# ──────────────────────────────────────────────────────────────────────────────
# release
# ──────────────────────────────────────────────────────────────────────────────


def _record_leased(db_path: Path, *, lease_id: str) -> str:
    """Write one `cell.leased` event with no matching `cell.released` (an open lease).

    Returns:
        The freshly minted Cell id the event names as `subject_id`.
    """
    trail = open_trail(db_path)
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    event = CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="cell.leased",
        subject_id=cell_id,
        payload={
            "lease_id": lease_id,
            "holder": new_warden_id(clock),
            "task_id": None,
            "access_level": "FULL",
            "comb_shield": "MEADOW",
        },
    )
    asyncio.run(trail.record(event))
    return cell_id


def test_release_reports_an_unknown_lease_orphaned(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(
        app, ["cells", "release", "no-such-lease", "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 1


def test_release_with_no_queen_running_reports_orphaned(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    _record_leased(_db_path(manifest_path), lease_id="lease-1")

    result = runner.invoke(app, ["cells", "release", "lease-1", "--manifest", str(manifest_path)])

    assert result.exit_code == 1
    assert "orphaned" in result.output
    assert "abscond" in result.output


def test_release_writes_a_durable_order_when_a_queen_looks_running(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    db_path = _db_path(manifest_path)
    _record_leased(db_path, lease_id="lease-1")
    trail = open_trail(db_path)
    clock = FakeClock()
    warden_event = WardenEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="warden.active",
        subject_id=new_warden_id(clock),
        payload={},
    )
    asyncio.run(trail.record(warden_event))

    result = runner.invoke(app, ["cells", "release", "lease-1", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    order_id = result.output.strip()
    store = open_cluster_orders(db_path)
    pending = asyncio.run(store.pending())
    assert len(pending) == 1
    assert pending[0].id == order_id
    assert pending[0].kind.value == "RELEASE"
    assert pending[0].lease_id == "lease-1"


# ──────────────────────────────────────────────────────────────────────────────
# snapshot / rollback (FakeCellBackend declares can_snapshot=False: NoopSnapshotter throughout)
# ──────────────────────────────────────────────────────────────────────────────


def test_snapshot_on_a_non_snapshotting_backend_prints_the_noop_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    manifest = _with_fake_backend(manifest_path)
    parts, cell_id = _seed_virtual_cell(manifest)
    _patch(monkeypatch, parts)

    result = runner.invoke(app, ["cells", "snapshot", cell_id, "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    # The last line is the printed snapshot id; a NoopSnapshotter also logs a warning first
    # (hivemind.cell.snapshot.NoopSnapshotter's own docstring), which CliRunner captures too.
    assert result.output.strip().splitlines()[-1] == "snap_noop"


def test_rollback_on_a_non_snapshotting_backend_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    manifest = _with_fake_backend(manifest_path)
    parts, cell_id = _seed_virtual_cell(manifest)
    _patch(monkeypatch, parts)

    result = runner.invoke(
        app, ["cells", "rollback", cell_id, "snap_noop", "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 1


# ──────────────────────────────────────────────────────────────────────────────
# abscond
# ──────────────────────────────────────────────────────────────────────────────


def test_abscond_refuses_without_confirmation(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["cells", "abscond", "--manifest", str(manifest_path)], input="n\n")

    assert result.exit_code == 1


def test_abscond_leaves_zero_containers_and_zero_open_leases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    manifest = _with_fake_backend(manifest_path)
    parts, _cell_id = _seed_virtual_cell(manifest)
    _patch(monkeypatch, parts)
    db_path = _db_path(manifest_path)
    lease_id = "lease-abscond-1"
    _record_leased(db_path, lease_id=lease_id)
    scratch_root = manifest.resolve_path(manifest.hive_stand.scratch_root) / lease_id
    scratch_root.mkdir(parents=True, exist_ok=True)
    (scratch_root / "marker.txt").write_text("left behind", encoding="utf-8")

    result = runner.invoke(app, ["cells", "abscond", "--manifest", str(manifest_path), "--yes"])

    assert result.exit_code == 0, result.output
    assert "left_as_found:             True" in result.output
    remaining_cells = asyncio.run(parts.registry.get("fake").list_cells(manifest.hive.id))
    assert remaining_cells == ()
    assert not scratch_root.exists()


def test_abscond_with_nothing_to_clean_up_reports_left_as_found(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["cells", "abscond", "--manifest", str(manifest_path), "--yes"])

    assert result.exit_code == 0, result.output
    assert "containers_destroyed:      0" in result.output
    assert "left_as_found:             True" in result.output
