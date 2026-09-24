"""Tests for hivemind.cli.recordings: `hive recordings list|show|export`.

Fits into the Hive:
    Mirrors src/hivemind/cli/recordings.py (codingrules section 3). Drives the typer application
    through typer.testing.CliRunner against a real fake_manifest and the real SQLite file it
    names, seeded through SqliteRecordingStore exactly as a running Warden would write it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.recordings for the module under test.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from builders.cli import fake_manifest
from builders.recordings import (
    HOME_URL,
    RECORDING_START,
    make_recorded_action,
    make_recording_info,
)
from typer.testing import CliRunner

from hivemind.cli.app import app
from hivemind.common.sqlite import connect
from hivemind.exoskeleton.recorder import (
    RecordedAction,
    RecordingInfo,
    RecordingSummary,
    SqliteRecordingStore,
    summarize,
)
from waggle.clock import FakeClock

runner = CliRunner()


@dataclass(frozen=True, slots=True)
class _Seeded:
    """A manifest whose database holds two recordings: an empty older one, a newer one."""

    manifest: Path
    db: Path
    older: RecordingInfo
    newer: RecordingInfo
    actions: tuple[RecordedAction, ...]  # The newer recording's actions, in order.


def _seed(tmp_path: Path) -> _Seeded:
    """Write fake_manifest and seed its `[hive] db` the way a Warden's recorder would."""
    manifest = fake_manifest(tmp_path)
    db = tmp_path / "data" / "hive.sqlite3"
    older = make_recording_info("rec_old", cell_id="cell_a", started_at=RECORDING_START)
    newer = make_recording_info(
        "rec_new", cell_id="cell_b", started_at=RECORDING_START + timedelta(hours=1)
    )
    actions = (
        make_recorded_action("p_signin"),
        make_recorded_action("p_blank", with_frames=False, state="REJECTED"),
    )

    async def _write() -> None:
        store = await SqliteRecordingStore.create(connect(db), FakeClock())
        for info in (older, newer):
            await store.open(info)
        for action in actions:
            await store.add(newer.recording_id, action)

    asyncio.run(_write())
    return _Seeded(manifest=manifest, db=db, older=older, newer=newer, actions=actions)


def _frame_data(actions: tuple[RecordedAction, ...]) -> list[str]:
    """Every frame's base64, as it would look if a frame's bytes leaked into text."""
    sides = [side for a in actions for side in (a.before, a.after) if side is not None]
    return [base64.b64encode(s.frame.png).decode("ascii") for s in sides if s.frame is not None]


def test_list_prints_recordings_newest_first(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)

    result = runner.invoke(app, ["recordings", "list", "--manifest", str(seeded.manifest)])

    assert result.exit_code == 0, result.output
    header, first, second = result.output.strip().splitlines()
    assert header.split() == ["RECORDING", "CELL", "TASK", "CLEARANCE", "STARTED"]
    assert first.split()[:2] == ["rec_new", "cell_b"]
    assert second.split()[:2] == ["rec_old", "cell_a"]


def test_list_json_prints_recording_headers(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)

    result = runner.invoke(
        app, ["recordings", "list", "--manifest", str(seeded.manifest), "--json"]
    )

    assert result.exit_code == 0, result.output
    rows = [RecordingInfo.model_validate(row) for row in json.loads(result.output)]
    assert rows == [seeded.newer, seeded.older]


def test_list_filters_by_cell_and_limits_through_the_db_escape_hatch(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)

    by_cell = runner.invoke(
        app, ["recordings", "list", "--db", str(seeded.db), "--cell", "cell_a", "--json"]
    )
    limited = runner.invoke(app, ["recordings", "list", "--db", str(seeded.db), "--limit", "1"])

    assert by_cell.exit_code == 0 and limited.exit_code == 0
    assert [row["recording_id"] for row in json.loads(by_cell.output)] == ["rec_old"]
    assert [line.split()[0] for line in limited.output.strip().splitlines()[1:]] == ["rec_new"]


def test_show_prints_every_action_with_frames_only_as_digests(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)

    result = runner.invoke(
        app, ["recordings", "show", "rec_new", "--manifest", str(seeded.manifest)]
    )

    assert result.exit_code == 0, result.output
    output = result.output
    assert "#1  VERIFIED" in output and "#2  REJECTED" in output
    assert output.index("p_signin") < output.index("p_blank")
    frame = seeded.actions[0].before.frame
    assert frame is not None and f"6x4 sha256:{frame.sha256[:12]}" in output
    assert f"url={HOME_URL}" in output and "URL_MATCHES page: held" in output
    assert "no frame" in output
    # Pixels never reach the terminal, in any encoding.
    assert "data:image" not in output
    assert not any(data in output for data in _frame_data(seeded.actions))


def test_show_json_prints_the_playback_summary(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)

    result = runner.invoke(app, ["recordings", "show", "rec_new", "--db", str(seeded.db), "--json"])

    assert result.exit_code == 0, result.output
    summary = RecordingSummary.model_validate_json(result.output)
    assert summary == summarize(seeded.newer, seeded.actions)


def test_show_an_unknown_recording_exits_1_naming_it(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)

    result = runner.invoke(app, ["recordings", "show", "rec_nope", "--db", str(seeded.db)])

    assert result.exit_code == 1
    assert "No flight recording with id 'rec_nope' exists." in result.output


def test_export_writes_the_page_and_the_summary(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)
    out = tmp_path / "exported" / "here"

    result = runner.invoke(
        app, ["recordings", "export", "rec_new", "--out", str(out), "--db", str(seeded.db)]
    )

    assert result.exit_code == 0, result.output
    page = (out / "rec_new.html").read_text(encoding="utf-8")
    summary = RecordingSummary.model_validate_json((out / "rec_new.json").read_text("utf-8"))
    assert all(f"base64,{data}" in page for data in _frame_data(seeded.actions))
    assert summary == summarize(seeded.newer, seeded.actions)
    assert "exported 2 actions to" in result.output
    assert not any(data in result.output for data in _frame_data(seeded.actions))


def test_export_an_unknown_recording_exits_1_and_writes_nothing(tmp_path: Path) -> None:
    seeded = _seed(tmp_path)
    out = tmp_path / "exported"

    result = runner.invoke(
        app, ["recordings", "export", "rec_nope", "--out", str(out), "--db", str(seeded.db)]
    )

    assert result.exit_code == 1
    assert not out.exists()
