"""Unit tests for hivemind.cell.local.releaser: HiveStandLeaseReleaser and kill_process_tree.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/releaser.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). `test_left_as_found.py` (a sibling module) covers the full kill-a-real-process
    path end to end; this module covers restore-record replay and residual-path bookkeeping.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.releaser for the module under test.
    - hivemind.cell.lease for RestoreRecord, replayed here.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest
from builders.cells import make_real_cell_lease

from hivemind.cell.lease import RestoreRecord
from hivemind.cell.local.releaser import HiveStandLeaseReleaser, kill_process_tree
from waggle.clock import FakeClock, SystemClock


async def test_kill_process_tree_returns_false_for_a_pid_that_is_already_dead() -> None:
    clock = SystemClock()
    pid = await _spawn_and_wait_exit()

    killed = await kill_process_tree(pid, clock)

    assert killed is False


async def test_release_replays_restore_records_in_reverse_skipping_persisted_ones(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    lease = make_real_cell_lease(
        tmp_path / "scratch", clock=clock, releaser=HiveStandLeaseReleaser(clock)
    )
    outside_a = tmp_path / "outside-a.txt"
    outside_b = tmp_path / "outside-b.txt"
    outside_persisted = tmp_path / "outside-persisted.txt"
    outside_a.write_bytes(b"new-a")
    outside_b.write_bytes(b"new-b")
    outside_persisted.write_bytes(b"new-persisted")
    lease.note_restore_path(outside_a, b"old-a")
    lease.note_restore_path(outside_b, None)  # Did not exist before; release() must delete it.
    # note_restore_path has no way to mark persist=True (only a later Capping dispatch, roadmap
    # step 3.17, sets it once the operator approves); this test builds that record directly.
    lease._restore_records.append(  # The only way to get a persist=True record for this test.
        RestoreRecord(
            path=outside_persisted.resolve(strict=False), prior=b"old-persisted", persist=True
        )
    )
    await lease.open()

    report = await lease.release()

    assert outside_a.read_bytes() == b"old-a"
    assert not outside_b.exists()
    assert outside_persisted.read_bytes() == b"new-persisted"  # Left alone: persist=True.
    assert report.residual_paths == ()
    assert report.is_restored


async def test_release_reports_a_path_that_cannot_be_restored_as_residual(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    lease = make_real_cell_lease(
        tmp_path / "scratch", clock=clock, releaser=HiveStandLeaseReleaser(clock)
    )
    # A directory in place of the path release() must write to: write_bytes must fail there.
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    lease.note_restore_path(blocked, b"cannot possibly write here")
    await lease.open()

    report = await lease.release()

    assert report.residual_paths == (blocked.resolve(strict=False),)
    assert report.is_restored is False


async def test_release_reports_the_scratch_dir_as_residual_when_removal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    lease = make_real_cell_lease(scratch_root, clock=clock, releaser=HiveStandLeaseReleaser(clock))
    await lease.open()

    def _boom(path: object) -> None:
        raise OSError("simulated: still in use")

    monkeypatch.setattr(shutil, "rmtree", _boom)

    report = await lease.release()

    assert scratch_root in report.residual_paths
    assert report.is_restored is False


async def _spawn_and_wait_exit() -> int:
    """Start and wait out a trivial child process, returning its now-dead pid."""
    if sys.platform == "win32":
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
    else:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "pass", start_new_session=True
        )
    await process.wait()
    return process.pid
