"""Unit tests for hivemind.cell.local.releaser: HiveStandLeaseReleaser and kill_process_tree.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/releaser.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). `test_left_as_found.py` (a sibling module) covers the full kill-a-real-process
    path end to end; this module covers restore-record replay, residual-path bookkeeping, and
    (roadmap step 5.0a) the Leavings ledger a `persist=True` record now produces, including the
    two coordinator-review bug fixes: a path ledgered twice (within one lease, or across two
    leases sharing a store) must never fail release(), and a vanished persisted path with a real
    `prior` must still be restored, never silently dropped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.releaser for the module under test.
    - hivemind.cell.lease for RestoreRecord, replayed here.
    - hivemind.cell.leavings for Leaving and InMemoryLeavingsStore, this module's own ledger.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sys
from pathlib import Path

import pytest
from builders.cells import make_hive_stand_releaser, make_identity, make_real_cell_lease

from hivemind.cell.lease import LeaseFacts, LeaseReleaser, RealCellLease
from hivemind.cell.leavings import ApprovedBy, InMemoryLeavingsStore
from hivemind.cell.local.releaser import kill_process_tree
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.pheromone import PheromoneTrail, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import Clock, FakeClock, SystemClock
from waggle.ids import CellId, new_cell_id, new_lease_id, new_warden_id

_REASON = "operator asked for the report to stay"  # A stand-in persist=True reason, tests-wide.


def _lease_on_cell(
    cell_id: CellId,
    scratch_root: Path,
    clock: Clock,
    releaser: LeaseReleaser,
    trail: PheromoneTrail,
) -> RealCellLease:
    """Build a REQUESTED RealCellLease pinned to `cell_id`, unlike `make_real_cell_lease`.

    `make_real_cell_lease` mints a fresh `cell_id` every call; this builds two leases on the
    *same* Cell instead, to simulate the "same goal run twice" case bug 1b covers.
    """
    facts = LeaseFacts(
        id=new_lease_id(clock),
        cell_id=cell_id,
        holder=new_warden_id(clock),
        task_id=None,
        scratch_root=scratch_root,
        access_level=AccessLevel.SCRATCH,
        comb_shield=CombShieldLevel.MEADOW,
        allowed_paths=(),
    )
    return RealCellLease(
        facts, trail=trail, clock=clock, identity=make_identity(clock=clock), releaser=releaser
    )


async def test_kill_process_tree_returns_false_for_a_pid_that_is_already_dead() -> None:
    clock = SystemClock()
    pid = await _spawn_and_wait_exit()

    killed = await kill_process_tree(pid, clock)

    assert killed is False


async def test_release_restores_ordinary_records_and_leaves_a_persisted_one(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    lease = make_real_cell_lease(
        tmp_path / "scratch", clock=clock, releaser=make_hive_stand_releaser(clock=clock)
    )
    outside_a = tmp_path / "outside-a.txt"
    outside_b = tmp_path / "outside-b.txt"
    outside_persisted = tmp_path / "outside-persisted.txt"
    outside_a.write_bytes(b"new-a")
    outside_b.write_bytes(b"new-b")
    outside_persisted.write_bytes(b"new-persisted")
    lease.note_restore_path(outside_a, b"old-a")
    lease.note_restore_path(outside_b, None)  # Did not exist before; release() must delete it.
    lease.note_restore_path(
        outside_persisted,
        b"old-persisted",
        persist=True,
        approved_by=ApprovedBy.POLICY,
        reason=_REASON,
    )
    await lease.open()

    report = await lease.release()

    assert outside_a.read_bytes() == b"old-a"
    assert not outside_b.exists()
    assert outside_persisted.read_bytes() == b"new-persisted"  # Left alone: persist=True.
    assert report.residual_paths == ()
    assert report.left_paths == (outside_persisted.resolve(strict=False),)
    assert report.is_restored  # A left path never counts against is_restored (roadmap 5.0a).


async def test_release_writes_a_leaving_and_a_cell_left_event_for_a_persisted_record(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    leavings = InMemoryLeavingsStore(trail)
    releaser = make_hive_stand_releaser(clock=clock, leavings=leavings)
    lease = make_real_cell_lease(tmp_path / "scratch", clock=clock, releaser=releaser, trail=trail)
    outside_persisted = tmp_path / "outside-persisted.txt"
    content = b"the report the task asked to keep"
    outside_persisted.write_bytes(content)
    lease.note_restore_path(
        outside_persisted, None, persist=True, approved_by=ApprovedBy.HUMAN, reason=_REASON
    )
    await lease.open()

    await lease.release()

    leaving = await leavings.get_leaving(lease.cell_id, outside_persisted.resolve(strict=False))
    assert leaving.sha256 == hashlib.sha256(content).hexdigest()
    assert leaving.size == len(content)
    assert leaving.task_id == lease.task_id
    assert leaving.lease_id == lease.id
    assert leaving.approved_by is ApprovedBy.HUMAN
    assert leaving.reason == _REASON
    assert leaving.removed_at is None
    events = await trail.query(TrailQuery(family="cell"))
    assert any(event.kind == "cell.left" for event in events)


async def test_release_collapses_two_persisted_records_for_one_path_into_one_leaving(
    tmp_path: Path,
) -> None:
    """Bug 1a: one lease noting the same path persisted twice must never raise on release()."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    leavings = InMemoryLeavingsStore(trail)
    releaser = make_hive_stand_releaser(clock=clock, leavings=leavings)
    lease = make_real_cell_lease(tmp_path / "scratch", clock=clock, releaser=releaser, trail=trail)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"first content")
    lease.note_restore_path(
        outside, b"the true original", persist=True, approved_by=ApprovedBy.POLICY, reason="a"
    )
    outside.write_bytes(b"second content")  # A later capping proposal writes it again.
    lease.note_restore_path(
        outside, b"first content", persist=True, approved_by=ApprovedBy.HUMAN, reason="b"
    )
    await lease.open()

    report = await lease.release()

    assert report.left_paths == (outside.resolve(strict=False),)
    leaving = await leavings.get_leaving(lease.cell_id, outside.resolve(strict=False))
    # The earliest record's prior -- what the path held before this lease touched it at all --
    # never the intermediate "first content" the second record's own prior names.
    assert leaving.prior == b"the true original"
    assert leaving.sha256 == hashlib.sha256(b"second content").hexdigest()
    assert leaving.approved_by is ApprovedBy.HUMAN  # The group's last persist=True record.
    assert leaving.reason == "b"


@pytest.mark.parametrize("persist_order", [(False, True), (True, False)])
async def test_release_mixed_persist_records_for_one_path_end_up_left(
    tmp_path: Path, persist_order: tuple[bool, bool]
) -> None:
    """Bug 1a: a persist=False then persist=True write for one path (or the reverse) is left."""
    clock = FakeClock()
    lease = make_real_cell_lease(
        tmp_path / "scratch", clock=clock, releaser=make_hive_stand_releaser(clock=clock)
    )
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"final content")
    first_persist, second_persist = persist_order
    lease.note_restore_path(
        outside,
        b"the true original",
        persist=first_persist,
        approved_by=ApprovedBy.POLICY if first_persist else None,
        reason="a" if first_persist else None,
    )
    lease.note_restore_path(
        outside,
        b"not the true original",
        persist=second_persist,
        approved_by=ApprovedBy.POLICY if second_persist else None,
        reason="b" if second_persist else None,
    )
    await lease.open()

    report = await lease.release()

    # Left in place either way: the non-persisted record for this path must never be replayed
    # over it once any record for the path is persisted (coordinator review).
    assert outside.read_bytes() == b"final content"
    assert report.left_paths == (outside.resolve(strict=False),)
    assert report.residual_paths == ()


async def test_release_a_vanished_persisted_path_with_no_prior_is_neither_residual_nor_left(
    tmp_path: Path,
) -> None:
    """Bug 2: gone by release time, and nothing was there before either -- already as found."""
    clock = FakeClock()
    lease = make_real_cell_lease(
        tmp_path / "scratch", clock=clock, releaser=make_hive_stand_releaser(clock=clock)
    )
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"content the task later deleted itself")
    lease.note_restore_path(
        outside, None, persist=True, approved_by=ApprovedBy.POLICY, reason=_REASON
    )
    outside.unlink()  # The task's own later step removed it before release() ever ran.
    await lease.open()

    report = await lease.release()

    assert report.left_paths == ()
    assert report.residual_paths == ()
    assert report.is_restored


async def test_release_a_vanished_persisted_path_with_a_prior_is_restored(tmp_path: Path) -> None:
    """Bug 2: gone by release time, but something real was there before -- restore, don't drop."""
    clock = FakeClock()
    lease = make_real_cell_lease(
        tmp_path / "scratch", clock=clock, releaser=make_hive_stand_releaser(clock=clock)
    )
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"content the task later deleted itself")
    lease.note_restore_path(
        outside,
        b"what was there before",
        persist=True,
        approved_by=ApprovedBy.POLICY,
        reason=_REASON,
    )
    outside.unlink()  # The task's own later step removed it before release() ever ran.
    await lease.open()

    report = await lease.release()

    assert outside.read_bytes() == b"what was there before"
    assert report.left_paths == ()
    assert report.residual_paths == ()
    assert report.is_restored


async def test_release_a_second_lease_leaving_the_same_path_keeps_the_first_leases_prior(
    tmp_path: Path,
) -> None:
    """Bug 1b: the same goal run twice -- record_leaving must upsert, never raise, on release()."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    leavings = InMemoryLeavingsStore(trail)
    outside = tmp_path / "outside.txt"
    cell_id = new_cell_id(clock)  # Both leases are on the same Cell: one goal run, twice.

    first_lease = _lease_on_cell(
        cell_id,
        tmp_path / "scratch-1",
        clock,
        make_hive_stand_releaser(clock=clock, leavings=leavings),
        trail,
    )
    outside.write_bytes(b"first run's output")
    first_lease.note_restore_path(
        outside, b"the true original", persist=True, approved_by=ApprovedBy.POLICY, reason="run 1"
    )
    await first_lease.open()
    await first_lease.release()

    second_lease = _lease_on_cell(
        cell_id,
        tmp_path / "scratch-2",
        clock,
        make_hive_stand_releaser(clock=clock, leavings=leavings),
        trail,
    )
    outside.write_bytes(b"second run's output")
    second_lease.note_restore_path(
        outside, b"first run's output", persist=True, approved_by=ApprovedBy.HUMAN, reason="run 2"
    )
    await second_lease.open()

    report = await second_lease.release()  # Must not raise ConflictError.

    assert report.left_paths == (outside.resolve(strict=False),)
    leaving = await leavings.get_leaving(cell_id, outside.resolve(strict=False))
    assert leaving.prior == b"the true original"  # Never "first run's output": not the original.
    assert leaving.sha256 == hashlib.sha256(b"second run's output").hexdigest()
    assert leaving.reason == "run 2"


async def test_release_reports_a_path_that_cannot_be_restored_as_residual(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    lease = make_real_cell_lease(
        tmp_path / "scratch", clock=clock, releaser=make_hive_stand_releaser(clock=clock)
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
    lease = make_real_cell_lease(
        scratch_root, clock=clock, releaser=make_hive_stand_releaser(clock=clock)
    )
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


async def test_release_keeps_the_scratch_dir_and_says_so_when_keep_scratch_is_set(
    tmp_path: Path,
) -> None:
    """`[hive_stand] keep_scratch`: the files survive, and the report never claims a restore."""
    clock = FakeClock()
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    (scratch_root / "haiku_1.txt").write_text("kept", encoding="utf-8")
    lease = make_real_cell_lease(
        scratch_root,
        clock=clock,
        releaser=make_hive_stand_releaser(clock=clock, keep_scratch=True),
    )
    await lease.open()

    report = await lease.release()

    assert (scratch_root / "haiku_1.txt").read_text(encoding="utf-8") == "kept"
    assert scratch_root in report.residual_paths
    assert report.is_restored is False
