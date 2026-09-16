"""Tests for hivemind.cli.readback.cluster: `hive cluster [--provider]`, `status`, `hive wake`.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/cluster.py (codingrules section 3; the CLI test convention
    keeps this flat under tests/unit/cli, matching test_cells.py's own placement for a command
    whose source lives one package level deeper). Drives the typer application through
    `typer.testing.CliRunner`, against a real `builders.cli.fake_manifest` manifest and a real
    SQLite file, seeded directly through `hivemind.cli.stores.open_cluster_orders`/`open_trail`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.cluster for the module under test.
    - docs/adr/0024-clustering-protocol.md for the protocol these orders trigger.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.cli.app import app
from hivemind.cli.stores import open_cluster_orders, open_trail
from hivemind.pheromone import QueenEvent
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id

runner = CliRunner()


def _db_path(manifest_path: Path) -> Path:
    """Return the SQLite file `builders.cli.fake_manifest` wrote `[hive] db` as."""
    return manifest_path.parent / "data" / "hive.sqlite3"


def test_cluster_command_appends_a_cluster_order_naming_the_provider(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["cluster", "--manifest", str(manifest_path), "--provider", "fake"])

    assert result.exit_code == 0, result.output
    order_id = result.output.strip()

    store = open_cluster_orders(_db_path(manifest_path))
    pending = asyncio.run(store.pending())
    assert len(pending) == 1
    assert pending[0].id == order_id
    assert pending[0].kind.value == "CLUSTER"
    assert pending[0].provider == "fake"


def test_cluster_command_with_no_provider_names_none(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["cluster", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    store = open_cluster_orders(_db_path(manifest_path))
    pending = asyncio.run(store.pending())
    assert len(pending) == 1
    assert pending[0].provider is None


def test_wake_command_appends_a_wake_order(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["wake", "fake", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    store = open_cluster_orders(_db_path(manifest_path))
    pending = asyncio.run(store.pending())
    assert len(pending) == 1
    assert pending[0].kind.value == "WAKE"
    assert pending[0].provider == "fake"


def test_status_command_never_starts_a_queen_and_lists_the_pending_order(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    runner.invoke(app, ["cluster", "--manifest", str(manifest_path), "--provider", "fake"])

    result = runner.invoke(app, ["cluster", "status", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert "PENDING" in result.output
    assert "fake" in result.output


def test_status_command_reconstructs_handled_orders_from_the_trail(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    db_path = _db_path(manifest_path)
    trail = open_trail(db_path)
    clock = FakeClock()
    hive_id, node_id = new_hive_id(clock), new_node_id(clock)
    event = QueenEvent(
        id=new_event_id(clock),
        hive_id=hive_id,
        node_id=node_id,
        at=clock.now(),
        actor="system",
        kind="queen.clustered",
        subject_id=hive_id,
        payload={"provider": "fake", "task_ids": []},
    )
    asyncio.run(trail.record(event))

    result = runner.invoke(app, ["cluster", "status", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert "HANDLED" in result.output
    assert "queen.clustered" in result.output
    assert "fake" in result.output
