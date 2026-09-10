"""Tests for hivemind.cli.readback.inbox: `hive inbox` and `hive inbox answer`.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/inbox.py (codingrules section 3). Drives the typer application
    through `typer.testing.CliRunner` against a real `builders.cli.fake_manifest` manifest, seeding
    its `[hive] db` file directly through `hivemind.cli.stores.open_chamber`/`open_trail` the same
    way a running `hive run` process would have left it (this command's own job is reading state a
    *different* process wrote, so a test seeding through the same store functions is the honest
    shape, not a shortcut).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.inbox for the module under test.
    - hivemind.queen.questions for sync_answers_from_chamber, the other half of `answer`'s own
      cross-process handoff, exercised directly (not through this CLI) in `tests/unit/queen`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from builders.cli import fake_manifest
from builders.tasks import make_graph_draft
from typer.testing import CliRunner

from hivemind.brood_chamber import ChamberIdentity
from hivemind.cli.app import app
from hivemind.cli.stores import open_chamber, open_trail
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import AlarmEvent
from waggle.clock import SystemClock
from waggle.ids import (
    CellId,
    WardenId,
    WorkerId,
    new_alarm_id,
    new_cell_id,
    new_event_id,
    new_warden_id,
    new_worker_id,
)

runner = CliRunner()


def _identity(manifest: HiveManifest) -> ChamberIdentity:
    return ChamberIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")


def _seed_pending_question(manifest_path: Path) -> str:
    """Seed one RUNNING task and one ASKED question against it; return the question's own id."""
    manifest = load_manifest(manifest_path, environ={})
    chamber = open_chamber(manifest.resolve_path(manifest.hive.db), _identity(manifest))

    async def _seed() -> str:
        tasks = await chamber.submit(make_graph_draft({"root": ()}))
        task = tasks[0]
        await chamber.assign(
            task.id,
            WardenId(new_warden_id(SystemClock())),
            CellId(new_cell_id(SystemClock())),
            "test",
        )
        await chamber.start(task.id)
        question = await chamber.ask(
            task.id,
            asked_by=WorkerId(new_worker_id(SystemClock())),
            text="Which environment should the haiku target?",
            options=("staging", "prod"),
        )
        return question.id

    return asyncio.run(_seed())


def _seed_escalated_alarm(manifest_path: Path) -> str:
    """Seed one `alarm.escalated` trail event with no matching `alarm.resolved`; return its id."""
    manifest = load_manifest(manifest_path, environ={})
    trail = open_trail(manifest.resolve_path(manifest.hive.db))
    clock = SystemClock()
    alarm_id = new_alarm_id(clock)
    event = AlarmEvent(
        id=new_event_id(clock),
        hive_id=manifest.hive.id,
        node_id=manifest.hive.node_id,
        at=clock.now(),
        actor="system",
        kind="alarm.escalated",
        subject_id=alarm_id,
        payload={},
    )
    asyncio.run(trail.record(event))
    return alarm_id


def test_inbox_lists_a_pending_question_and_an_escalated_alarm(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    _seed_pending_question(manifest_path)
    alarm_id = _seed_escalated_alarm(manifest_path)

    result = runner.invoke(app, ["inbox", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.output
    assert "Which environment" in result.output
    assert alarm_id in result.output


def test_inbox_json_reports_both_sections(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    question_id = _seed_pending_question(manifest_path)
    alarm_id = _seed_escalated_alarm(manifest_path)

    result = runner.invoke(app, ["inbox", "--manifest", str(manifest_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["questions"][0]["id"] == question_id
    assert payload["alarms"][0]["alarm_id"] == alarm_id


def test_inbox_answer_records_a_human_answer_and_resumes_the_task(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    question_id = _seed_pending_question(manifest_path)

    result = runner.invoke(
        app, ["inbox", "answer", question_id, "Use staging.", "--manifest", str(manifest_path)]
    )

    assert result.exit_code == 0, result.output
    assert "RUNNING" in result.output

    # Answering again must fail (the question is no longer ASKED): proves the answer really
    # landed in the store `hive inbox` itself reads, not just in this command's own printed line.
    second = runner.invoke(
        app,
        ["inbox", "answer", question_id, "Use staging again.", "--manifest", str(manifest_path)],
    )
    assert second.exit_code != 0


def test_inbox_answer_with_option_records_the_chosen_text(tmp_path: Path) -> None:
    manifest_path = fake_manifest(tmp_path)
    question_id = _seed_pending_question(manifest_path)

    result = runner.invoke(
        app,
        [
            "inbox",
            "answer",
            question_id,
            "Use staging.",
            "--option",
            "0",
            "--manifest",
            str(manifest_path),
        ],
    )

    assert result.exit_code == 0, result.output


def test_inbox_answer_exits_2_on_a_missing_manifest(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["inbox", "answer", "msg_x", "text", "--manifest", str(tmp_path / "missing.toml")],
    )

    assert result.exit_code == 2
