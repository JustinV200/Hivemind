"""Tests for hivemind.cli.forage: `hive forage status|grants|grant`.

Fits into the Hive:
    Mirrors src/hivemind/cli/forage.py (codingrules section 3). Drives the typer application
    through `typer.testing.CliRunner`, against a real `builders.cli.fake_manifest` manifest and a
    real SQLite file, seeded directly through `hivemind.cli.stores.open_ledger`/`open_trail` the
    same way `test_cells.py`/`test_inbox.py` seed their own stores.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.forage for the module under test.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.cli.app import app
from hivemind.cli.stores import open_ledger, open_trail
from hivemind.forage import ForageGrant, GrantState
from hivemind.pheromone import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_warden_id

runner = CliRunner()


def _db_path(manifest_path: Path) -> Path:
    """Return the SQLite file `builders.cli.fake_manifest` wrote `[hive] db` as."""
    return manifest_path.parent / "data" / "hive.sqlite3"


def _seed_grant(db_path: Path) -> tuple[str, str]:
    """Insert one live ACTIVE grant directly into the ledger store; return (warden_id, grant_id).

    `open_ledger` runs its own internal `asyncio.run` (`hivemind.cli.stores`'s own docstring), so
    it is called here, synchronously, before the separate `asyncio.run(store.put_grant(grant))`
    below -- nesting one inside the other raises "cannot be called from a running event loop"
    (verified empirically while drafting this test).
    """
    clock = FakeClock()
    warden_id = new_warden_id(clock)
    grant = ForageGrant(
        id=new_grant_id(clock),
        holder=warden_id,
        cell_id=new_cell_id(clock),
        task_id=None,
        revision=0,
        allowed=(),
        seats=(),
        token_budget=1_000_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=2,
        expires_at=datetime.now(UTC) + timedelta(seconds=300),
        reason="Seeded by test_forage.py.",
        state=GrantState.ACTIVE,
    )
    store = open_ledger(db_path)
    asyncio.run(store.put_grant(grant))
    return warden_id, grant.id


def test_status_command_with_no_data_prints_zero_headroom(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["forage", "--manifest", str(manifest_path), "status"])

    assert result.exit_code == 0, result.output
    assert "headroom" in result.output


def test_grants_command_with_no_data_prints_an_empty_table(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["forage", "--manifest", str(manifest_path), "grants"])

    assert result.exit_code == 0, result.output
    assert "GRANT" in result.output


def test_grant_command_without_an_existing_grant_is_refused(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(
        app,
        [
            "forage",
            "--manifest",
            str(manifest_path),
            "grant",
            "wdn_nonexistent",
            "--sub-bees",
            "4",
        ],
    )

    assert result.exit_code != 0


def test_grant_command_revises_an_existing_grant_and_records_the_trail_event(
    tmp_path: Path,
) -> None:
    manifest_path = fake_manifest(tmp_path)
    db_path = _db_path(manifest_path)
    warden_id, grant_id = _seed_grant(db_path)

    result = runner.invoke(
        app,
        [
            "forage",
            "--manifest",
            str(manifest_path),
            "grant",
            warden_id,
            "--sub-bees",
            "8",
            "--spend",
            "12.5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert grant_id in result.output

    store = open_ledger(db_path)
    grants = asyncio.run(store.list_grants())
    assert len(grants) == 1
    assert grants[0].revision == 1
    assert grants[0].max_sub_bees == 8
    assert grants[0].spend_budget == 12.5

    trail = open_trail(db_path)
    events = asyncio.run(trail.query(TrailQuery(family="forage", kind="forage.granted")))
    assert len(events) == 1
    assert events[0].subject_id == grant_id
    assert events[0].payload["max_sub_bees"] == 8


def test_grants_command_json_lists_the_seeded_grant(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    warden_id, grant_id = _seed_grant(_db_path(manifest_path))

    result = runner.invoke(app, ["forage", "--manifest", str(manifest_path), "grants", "--json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 1
    assert rows[0]["grant_id"] == grant_id
    assert rows[0]["holder"] == warden_id
