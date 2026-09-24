"""Unit tests for hivemind.exoskeleton.recorder.sqlite: what the contract suite cannot see.

The shared clauses (order, round trip, not-found, listing, purge, prune) live in
tests/contracts/test_recording_store_contract.py; these check the SQLite store's own storage
promises against the raw tables of a real file: one migration series applied once, frames only in
BLOB columns and never in a JSON body, fixed-width UTC timestamps, per-recording action numbering,
no orphaned action rows, and a second connection reading what the first wrote.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from builders.recordings import RECORDING_START, make_recorded_action, make_recording_info

from hivemind.common.migrations import applied_versions
from hivemind.common.sqlite import connect
from hivemind.exoskeleton.recorder import SqliteRecordingStore, apply_recording_migrations
from hivemind.exoskeleton.recorder.sqlite import SUBSYSTEM
from waggle.clock import FakeClock


async def _store(db: Path) -> SqliteRecordingStore:
    """Open a store on `db` through its own connection."""
    return await SqliteRecordingStore.create(connect(db), FakeClock())


async def test_create_applies_the_migration_series_once(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    await _store(db)
    await _store(db)
    connection = connect(db)

    assert applied_versions(connection, SUBSYSTEM) == (1,)
    assert apply_recording_migrations(connection, FakeClock()) == ()


async def test_a_second_connection_reads_what_the_first_wrote(tmp_path: Path) -> None:
    # The Warden writes through `hive run`'s connection; `hive recordings` reads through its own.
    db = tmp_path / "hive.sqlite3"
    writer, reader = await _store(db), await _store(db)
    info, action = make_recording_info(), make_recorded_action()

    await writer.open(info)
    await writer.add(info.recording_id, action)

    assert await reader.info(info.recording_id) == info
    assert await reader.actions(info.recording_id) == (action,)


async def test_frames_live_only_in_blob_columns_never_in_the_json_body(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    store = await _store(db)
    info, action = make_recording_info(), make_recorded_action()
    await store.open(info)

    await store.add(info.recording_id, action)
    row = connect(db).execute("SELECT * FROM exoskeleton_recording_actions").fetchone()

    assert action.before.frame is not None and action.after is not None
    assert action.after.frame is not None
    assert row["before_png"] == action.before.frame.png
    assert row["after_png"] == action.after.frame.png
    # The body names no frame at all: neither the bytes, nor their base64, nor a PNG signature.
    body: str = row["body"]
    assert '"frame":null' in body.replace(" ", "")
    assert base64.b64encode(action.before.frame.png).decode("ascii") not in body
    assert "PNG" not in body
    # The typed steps ride in the body, the secret fill's text only ever the mask.
    assert '"text":"[redacted]"' in body.replace(" ", "")


async def test_timestamps_are_stored_as_fixed_width_utc_text(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    store = await _store(db)
    await store.open(make_recording_info(started_at=RECORDING_START))

    row = connect(db).execute("SELECT started_at FROM exoskeleton_recordings").fetchone()

    # Microseconds always present, so text order is time order at any precision.
    assert row["started_at"] == "2026-09-24T09:00:00.000000+00:00"


async def test_actions_are_numbered_from_one_within_each_recording(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    store = await _store(db)
    first, second = make_recording_info("rec_1"), make_recording_info("rec_2")
    for info in (first, second):
        await store.open(info)
    await store.add("rec_1", make_recorded_action("p_1"))
    await store.add("rec_2", make_recorded_action("p_2"))
    await store.add("rec_1", make_recorded_action("p_3"))

    rows = connect(db).execute(
        "SELECT recording_id, seq FROM exoskeleton_recording_actions ORDER BY recording_id, seq"
    )

    assert [(row["recording_id"], row["seq"]) for row in rows] == [
        ("rec_1", 1),
        ("rec_1", 2),
        ("rec_2", 1),
    ]


async def test_purge_cell_leaves_no_orphaned_action_rows(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    store = await _store(db)
    await store.open(make_recording_info("rec_v", cell_id="cell_v"))
    await store.add("rec_v", make_recorded_action())
    await store.add("rec_v", make_recorded_action("p_2"))

    await store.purge_cell("cell_v")
    count = connect(db).execute("SELECT COUNT(*) FROM exoskeleton_recording_actions").fetchone()

    assert count[0] == 0


async def test_prune_before_rejects_a_naive_cutoff(tmp_path: Path) -> None:
    store = await _store(tmp_path / "hive.sqlite3")

    # A naive datetime on purpose: its offset is unknown, so it cannot be compared.
    with pytest.raises(ValueError, match="timezone-aware"):
        await store.prune_before(datetime(2026, 9, 24))


async def test_prune_before_compares_instants_across_utc_offsets(tmp_path: Path) -> None:
    # A cutoff in another offset names the same instant: it is normalised to UTC before the text
    # comparison: a second before the start in +05:00 prunes nothing, a second after in -07:00 does.
    store = await _store(tmp_path / "hive.sqlite3")
    await store.open(make_recording_info(started_at=RECORDING_START))
    east, west = timezone(timedelta(hours=5)), timezone(timedelta(hours=-7))
    just_before = RECORDING_START.astimezone(east) - timedelta(seconds=1)
    just_after = RECORDING_START.astimezone(west) + timedelta(seconds=1)

    assert await store.prune_before(just_before) == 0
    assert await store.prune_before(just_after) == 1
