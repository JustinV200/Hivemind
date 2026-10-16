"""Tests for hivemind.cli.in_cell.borrowed.stores: the durable stores of a device Warden.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/stores.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.stores for the module under test.
"""

from __future__ import annotations

import sqlite3
import stat
import sys
from pathlib import Path

import pytest
from builders.memory import make_handoff

from hivemind.cell import HoneyClearance
from hivemind.cli.in_cell.borrowed.stores import DATABASE_FILE, open_device_stores
from hivemind.memory import (
    MemoryContext,
    MemoryIdentity,
    MemoryStore,
    read_handoff,
    write_checkpoint,
)
from hivemind.pheromone import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id, new_warden_id


def _context(store: MemoryStore, clock: FakeClock) -> MemoryContext:
    """The context a Warden writes memory with: its store, its identity and its clock."""
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor=new_warden_id(clock)
    )
    return MemoryContext(store=store, identity=identity, clock=clock)


async def test_a_handoff_written_by_one_run_is_read_by_the_next(tmp_path: Path) -> None:
    # The reason these stores are SQLite files: a paused task resumes from its Handoff, and the
    # Warden that resumes it may be a different process from the one that paused it.
    clock = FakeClock()
    state_dir = tmp_path / "state"
    first = await open_device_stores(state_dir, clock)
    handoff = make_handoff(clock)
    ref = await write_checkpoint(handoff, None, _context(first.memory, clock))

    second = await open_device_stores(state_dir, clock)  # The next process, over the same files.
    restored = await read_handoff(second.memory, ref, HoneyClearance.C2)
    checkpoints = await second.trail.query(TrailQuery(kind="memory.checkpoint"))

    assert restored == handoff
    assert len(checkpoints) == 1  # The trail segment is durable too, so it can be shipped late.


async def test_all_three_stores_share_one_file_under_the_state_directory(tmp_path: Path) -> None:
    state_dir = tmp_path / "nested" / "state"  # Parents are created as well.

    await open_device_stores(state_dir, FakeClock())

    assert (state_dir / DATABASE_FILE).is_file()
    assert [path.name for path in state_dir.glob("*.sqlite")] == [DATABASE_FILE]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits; Windows ignores them")
async def test_the_state_directory_is_private_to_the_warden_user(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"

    await open_device_stores(state_dir, FakeClock())

    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700


async def test_close_releases_the_file_and_a_second_close_is_nothing(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    stores = await open_device_stores(state_dir, FakeClock())
    assert (state_dir / f"{DATABASE_FILE}-wal").exists()  # Open: the WAL has a writer.

    await stores.close()
    await stores.close()

    # The WAL was checkpointed and removed with its last connection: nothing is left for the
    # packet's restart of the process to recover, and no store can be reached any more.
    assert [path.name for path in state_dir.iterdir()] == [DATABASE_FILE]
    with pytest.raises(sqlite3.ProgrammingError):
        await stores.trail.query(TrailQuery())
    with pytest.raises(sqlite3.ProgrammingError):
        await stores.leavings.list_all_leavings()
