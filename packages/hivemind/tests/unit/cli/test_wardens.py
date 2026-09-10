"""Tests for hivemind.cli.readback.wardens: `hive wardens list`.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/wardens.py (codingrules section 3). Drives the typer
    application through `typer.testing.CliRunner` against a real `builders.cli.fake_manifest`
    manifest, seeding its `[hive] db` file directly with `warden.*`/`forage.granted`/`worker.*`
    trail events the same way a running `hive run` process would have left them (this command's
    own job is reconstructing state a *different* process wrote, so a test seeding the trail
    directly is the honest shape, not a shortcut).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.wardens for the module under test.
    - hivemind.queen.dispatcher for the `forage.granted` `warden_id` payload field this command
      reads, added in this same dispatch.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from builders.cli import fake_manifest
from typer.testing import CliRunner

from hivemind.cli.app import app
from hivemind.cli.stores import open_trail
from hivemind.manifest import load_manifest
from hivemind.pheromone import ForageEvent, PheromoneEvent, WardenEvent, WorkerEvent
from waggle.clock import SystemClock
from waggle.ids import new_event_id, new_grant_id, new_warden_id, new_worker_id

runner = CliRunner()


def _seed_one_warden_with_a_grant_and_a_sub_bee(manifest_path: Path) -> tuple[str, str, str]:
    """Seed one Warden (ACTIVE), one grant issued to it, and one sub-bee (STARTED) under it.

    Returns:
        `(warden_id, grant_id, worker_id)`.
    """
    manifest = load_manifest(manifest_path, environ={})
    trail = open_trail(manifest.resolve_path(manifest.hive.db))
    clock = SystemClock()
    warden_id = new_warden_id(clock)
    grant_id = new_grant_id(clock)
    worker_id = new_worker_id(clock)

    def _event(
        family_cls: type[PheromoneEvent], kind: str, subject_id: str, payload: dict[str, object]
    ) -> PheromoneEvent:
        return family_cls(
            id=new_event_id(clock),
            hive_id=manifest.hive.id,
            node_id=manifest.hive.node_id,
            at=clock.now(),
            actor="system",
            kind=kind,
            subject_id=subject_id,
            payload=payload,
        )

    async def _seed() -> None:
        await trail.record(_event(WardenEvent, "warden.started", warden_id, {}))
        await trail.record(_event(WardenEvent, "warden.active", warden_id, {}))
        await trail.record(
            _event(
                ForageEvent,
                "forage.granted",
                grant_id,
                {"task_id": "t_x", "warden_id": warden_id},
            )
        )
        await trail.record(_event(WorkerEvent, "worker.spawned", worker_id, {}))
        await trail.record(_event(WorkerEvent, "worker.started", worker_id, {}))

    asyncio.run(_seed())
    return warden_id, grant_id, worker_id


def test_wardens_list_prints_the_wardens_state_grant_and_sub_bee(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    warden_id, _grant_id, worker_id = _seed_one_warden_with_a_grant_and_a_sub_bee(manifest_path)

    result = runner.invoke(app, ["wardens", "list", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert warden_id in result.output
    assert "warden.active" in result.output
    assert "1" in result.output  # One grant issued.
    assert f"{worker_id}:worker.started" in result.output


def test_wardens_list_json_reports_the_expected_fields(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    warden_id, grant_id, worker_id = _seed_one_warden_with_a_grant_and_a_sub_bee(manifest_path)

    result = runner.invoke(app, ["wardens", "list", "--manifest", str(manifest_path), "--json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert len(rows) == 1
    row = rows[0]
    assert row["warden_id"] == warden_id
    assert row["state"] == "warden.active"
    assert row["grant_ids"] == [grant_id]
    assert row["sub_bees"] == [f"{worker_id}:worker.started"]


def test_wardens_list_is_empty_when_no_warden_has_ever_been_seen(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)

    result = runner.invoke(app, ["wardens", "list", "--manifest", str(manifest_path), "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_wardens_list_exits_2_on_a_missing_manifest(tmp_path: Path) -> None:
    result = runner.invoke(app, ["wardens", "list", "--manifest", str(tmp_path / "missing.toml")])

    assert result.exit_code == 2
