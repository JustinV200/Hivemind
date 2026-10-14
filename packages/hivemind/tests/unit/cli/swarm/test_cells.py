"""Tests for hivemind.cli.swarm.cells and its use by `hive cells list`: devices as Real Cells.

Fits into the Hive:
    Mirrors src/hivemind/cli/swarm/cells.py (codingrules section 3). The pure rule that reads a
    Warden's mode off its trail edges is tested directly; the rows are tested through the real
    typer app against a real manifest, registry and trail (`harness`): a Swarm node appears as a
    REAL Cell of source `swarm` at each rung of the runtime ladder, with the Warden's place from
    the Fleet view's own rule, the Warden's mode from the newest `warden.*` mode edge, and the
    device's terms; a revoked device is not a Cell; and a manifest that keeps the Swarm off lists
    only the Hive Stand and makes no file.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.swarm.cells and hivemind.cli.readback.cells for the modules under test.
    - hivemind.observation.api.fleet for the Warden-location rule both share.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from builders.cells import make_identity
from unit.cli.swarm.harness import db_path, enrol, invoke_cells, make_hive

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.cli.stores import open_trail
from hivemind.cli.swarm.cells import MODE_UNKNOWN, mode_of
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, WardenEvent
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from waggle.clock import FakeClock
from waggle.ids import new_event_id
from waggle.messages.swarm import RuntimeLevel


def _edge(manifest: Path, node: SwarmNode, kind: str, clock: FakeClock) -> PheromoneEvent:
    """Build a `warden.*` event about the node's Warden, as the Hive's trail would hold it."""
    loaded = load_manifest(manifest, {})
    identity = make_identity(clock, hive_id=loaded.hive.id, node_id=loaded.hive.node_id)
    return WardenEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=node.warden_id,
        payload={},
    )


def _record_edges(manifest: Path, node: SwarmNode, *kinds: str) -> None:
    """Record these `warden.*` edges for the node's Warden on the Hive's trail, oldest first."""
    clock = FakeClock()
    trail = open_trail(db_path(manifest))
    for kind in kinds:
        clock.advance(1.0)
        asyncio.run(trail.record(_edge(manifest, node, kind, clock)))


def _rows(manifest: Path) -> list[dict[str, object]]:
    """The Cells `hive cells list --json` prints, the Hive Stand's first."""
    result = invoke_cells(manifest, "--json")
    assert result.exit_code == 0, result.stderr
    rows: list[dict[str, object]] = json.loads(result.stdout)
    return rows


def _swarm_rows(manifest: Path) -> list[dict[str, object]]:
    """Only the rows whose source is `swarm`."""
    return [row for row in _rows(manifest) if row["source"] == "swarm"]


def test_a_swarm_node_is_a_real_cell_of_source_swarm_with_its_own_terms(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(
        manifest,
        label="kitchen-pi",
        access_level=AccessLevel.READ_ONLY,
        comb_shield=CombShieldLevel.PROPOLIS,
    )

    rows = _rows(manifest)

    assert [row["source"] for row in rows] == ["hive_stand", "swarm"]
    (row,) = _swarm_rows(manifest)
    assert row["id"] == node.cell_id and row["name"] == "kitchen-pi" and row["kind"] == "REAL"
    assert (row["access_level"], row["comb_shield"]) == ("READ_ONLY", "PROPOLIS")
    assert row["cores"] == node.capacity.host.cores and row["enabled"] is True


@pytest.mark.parametrize(
    ("level", "warden_runs"),
    [
        (RuntimeLevel.GATEWAY, "hive_stand"),
        (RuntimeLevel.COLONIZED, "on_cell"),
        (RuntimeLevel.NUC, "on_cell"),
    ],
)
def test_a_swarm_node_at_each_rung_says_where_its_warden_runs(
    tmp_path: Path, level: RuntimeLevel, warden_runs: str
) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi", level=level)

    (row,) = _swarm_rows(manifest)

    assert (row["level"], row["warden_runs"]) == (level.value, warden_runs)


def test_the_hive_stand_row_runs_its_warden_on_the_hive_stand_and_has_no_rung_or_mode(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)

    (stand,) = _rows(manifest)

    assert stand["source"] == "hive_stand" and stand["warden_runs"] == "hive_stand"
    assert stand["level"] is None and stand["mode"] is None


def test_the_table_has_the_warden_level_and_mode_columns(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi", level=RuntimeLevel.COLONIZED)

    result = invoke_cells(manifest)

    header, stand, device = result.stdout.splitlines()
    assert header.split()[5:9] == ["SHIELD", "WARDEN", "LEVEL", "MODE"]
    assert stand.split()[6:7] == ["hive_stand"] and "swarm" in device
    assert device.split()[6:9] == ["on_cell", "COLONIZED", "UNKNOWN"]


@pytest.mark.parametrize(
    ("edges", "mode"),
    [
        ((), "UNKNOWN"),
        (("warden.started", "warden.active"), "ACTIVE"),
        (("warden.active", "warden.watch"), "WATCH"),
        (("warden.watch", "warden.offline"), "OFFLINE"),
        (("warden.offline", "warden.reconnected", "warden.active"), "ACTIVE"),
        (("warden.active", "warden.clustered"), "UNKNOWN"),
        (("warden.watch", "warden.migrated"), "UNKNOWN"),
        (("warden.active", "warden.stopped"), "UNKNOWN"),
    ],
)
def test_the_mode_is_the_newest_warden_mode_edge_on_the_trail(
    tmp_path: Path, edges: tuple[str, ...], mode: str
) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    _record_edges(manifest, node, *edges)

    (row,) = _swarm_rows(manifest)

    assert row["mode"] == mode


def test_one_wardens_edges_never_set_another_wardens_mode(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    watching = enrol(manifest, label="attic-pc")
    enrol(manifest, label="kitchen-pi")
    _record_edges(manifest, watching, "warden.watch")

    modes = {row["name"]: row["mode"] for row in _swarm_rows(manifest)}

    assert modes == {"attic-pc": "WATCH", "kitchen-pi": "UNKNOWN"}


def test_an_unreachable_device_is_listed_but_not_enabled(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi", status=NodeStatus.UNREACHABLE)

    (row,) = _swarm_rows(manifest)

    assert row["enabled"] is False


def test_a_revoked_device_is_no_longer_a_cell(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    enrol(manifest, label="kitchen-pi", status=NodeStatus.REVOKED, revoked_at=FakeClock().now())
    enrol(manifest, label="attic-pc")

    assert [row["name"] for row in _swarm_rows(manifest)] == ["attic-pc"]


def test_a_manifest_with_the_swarm_off_lists_only_the_hive_stand_and_makes_no_database(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path, enabled=False)

    rows = _rows(manifest)

    assert [row["source"] for row in rows] == ["hive_stand"]
    assert not db_path(manifest).exists()


def test_a_swarm_that_is_on_but_has_never_run_lists_only_the_hive_stand_and_makes_no_database(
    tmp_path: Path,
) -> None:
    manifest = make_hive(tmp_path)

    assert [row["source"] for row in _rows(manifest)] == ["hive_stand"]
    assert not db_path(manifest).exists()


def test_mode_of_skips_events_that_say_nothing_about_a_mode(tmp_path: Path) -> None:
    manifest = make_hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    clock = FakeClock()
    newest_first = [
        _edge(manifest, node, kind, clock)
        for kind in ("warden.patrol", "warden.intervened", "warden.watch", "warden.active")
    ]

    assert mode_of(newest_first) == "WATCH"
    assert mode_of(newest_first[:2]) == MODE_UNKNOWN
    assert mode_of([]) == MODE_UNKNOWN
