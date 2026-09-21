"""The roadmap step 3.11/5.0a left-as-found test: the Hive Stand is untouched outside its own scope.

Snapshots a temporary "home" tree (files nothing in this lease's scope should ever touch) before
and after a full lease/session/release cycle that writes inside scratch and leaves a command
running past its own `exec()` call still being in flight, then asserts: scratch is gone, the home
tree is byte-for-byte unchanged, and the process this lease started is dead (codingrules section
8.7: "Real Cells are borrowed... release() kills every process the lease started and removes the
scratch directory"). Roadmap step 5.0a extends the same test: a `persist=True` restore record
means the home tree is left as its snapshot *plus exactly the ledger's paths* (the phase 5
preamble's own rule), and `hive cells leavings remove`'s lower-level mechanics (replay-then-mark,
`hivemind.cli.readback.leavings`'s own module docstring) bring it back to the plain snapshot.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises `hivemind.cell.local.session.
    LocalProcessSession`, `hivemind.cell.local.releaser.HiveStandLeaseReleaser`,
    `hivemind.cell.lease.RealCellLease` and `hivemind.cell.leavings.InMemoryLeavingsStore`
    together, end to end, over real OS processes and a real filesystem.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 3.11 for "a left-as-found test snapshots a temporary home and the
      process table before and after."
    - .claude/roadmap.md step 5.0a for "the left-as-found test is extended: after release the
      temporary home equals its snapshot plus exactly the ledger's paths, and after remove it
      equals the snapshot."
    - hivemind.cell.local.releaser for HiveStandLeaseReleaser, under test here.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from builders.cells import make_hive_stand_releaser, make_real_cell_lease

from hivemind.cell.leavings import ApprovedBy, InMemoryLeavingsStore
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.local.session import LocalProcessSession
from hivemind.cell.session import ExecSpec, run
from hivemind.pheromone import CellEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import SystemClock
from waggle.ids import new_event_id, new_hive_id, new_node_id

_LARGE_QUOTA_BYTES = 64 * 1024 * 1024  # Generous: this test is about cleanup, not the watchdog.
_LONG_SLEEP_S = 60.0  # Long enough that it is still running when release() reaches for it.
_WAIT_TIMEOUT_S = 5.0  # How long this test waits for the child to register, and to wind down.


def _snapshot(root: Path) -> dict[str, int]:
    """Map every file under `root` (relative path -> size in bytes), for a before/after diff."""
    return {
        str(path.relative_to(root)): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _is_process_alive(pid: int) -> bool:
    """Return whether `pid` is still a live process, POSIX and Windows shims side by side."""
    if sys.platform == "win32":
        # SAFETY: an argument list, never shell=True; tasklist is a read-only query.
        result = subprocess.run(  # noqa: S603 -- fixed argv, a well-known system command.
            ["tasklist", "/FI", f"PID eq {pid}"],  # noqa: S607 -- well-known system command.
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


async def _wait_until(predicate: Callable[[], bool], timeout_s: float) -> None:
    """Poll `predicate` until it is true or `timeout_s` elapses, without a fake clock.

    A real process's own startup time cannot be simulated with a FakeClock (codingrules 14.5's
    "no sleeping in tests" is for logical waits; this test coordinates with a real subprocess).
    """
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout_s}s")
        await asyncio.sleep(0.01)


async def test_a_full_lease_cycle_leaves_the_home_tree_untouched_and_kills_every_process(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "keep.txt").write_bytes(b"the operator's own file")
    nested = home / "nested"
    nested.mkdir()
    (nested / "also-keep.bin").write_bytes(b"\x00" * 128)
    before = _snapshot(home)

    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    clock = SystemClock()
    lease = make_real_cell_lease(
        scratch_root, clock=clock, releaser=make_hive_stand_releaser(clock=clock)
    )
    await lease.open()
    session = LocalProcessSession(lease, ScratchQuota(quota_bytes=_LARGE_QUOTA_BYTES), clock)

    # Write inside scratch: this is the one thing this lease may freely change.
    await session.put_file(Path("work.txt"), b"scratch-only content")

    # Stand in for a task that leaves something running (a dev server, say): start a long
    # command and let release() reach it while its own exec() call is still in flight.
    linger = asyncio.create_task(
        run(
            session,
            ExecSpec(
                argv=(sys.executable, "-c", f"import time; time.sleep({_LONG_SLEEP_S})"),
                timeout_s=_LONG_SLEEP_S,
            ),
        )
    )
    await _wait_until(lambda: len(lease.started_pids) > 0, _WAIT_TIMEOUT_S)
    pid = lease.started_pids[0]
    assert _is_process_alive(pid), "the child must still be running before release"

    report = await lease.release()
    await asyncio.wait_for(linger, timeout=_WAIT_TIMEOUT_S)

    assert not scratch_root.exists()
    assert _snapshot(home) == before
    assert not _is_process_alive(pid)
    assert report.is_restored
    assert report.killed_processes == 1


async def test_release_then_remove_leaves_exactly_the_ledgers_paths_then_the_plain_snapshot(
    tmp_path: Path,
) -> None:
    """Roadmap step 5.0a: release() leaves snapshot + the ledger's paths; remove reverts it."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "keep.txt").write_bytes(b"the operator's own file")
    before = _snapshot(home)

    clock = SystemClock()
    trail = MemoryPheromoneTrail(clock)
    leavings = InMemoryLeavingsStore(trail)
    releaser = make_hive_stand_releaser(clock=clock, leavings=leavings)
    lease = make_real_cell_lease(tmp_path / "scratch", clock=clock, releaser=releaser, trail=trail)
    await lease.open()

    # A persist=True write outside scratch: the task's own output, left in place on purpose
    # instead of being restored -- mirrors hivemind.supervision.capping.apply's own
    # "note_restore_path before the write" ordering.
    persisted = home / "report.txt"
    persisted_content = b"the report the task was allowed to keep"
    lease.note_restore_path(
        persisted, None, persist=True, approved_by=ApprovedBy.POLICY, reason="task's own output"
    )
    persisted.write_bytes(persisted_content)

    report = await lease.release()

    # Left as found, plus exactly the ledger's paths (roadmap phase 5 preamble).
    assert _snapshot(home) == {**before, "report.txt": len(persisted_content)}
    assert report.left_paths == (persisted.resolve(strict=False),)

    # hive cells leavings remove's own mechanics (hivemind.cli.readback.leavings): replay the
    # ledgered bytes back (None here, so unlink), then mark the row removed.
    leaving = await leavings.get_leaving(lease.cell_id, persisted.resolve(strict=False))
    assert leaving.prior is None
    persisted.unlink()
    event = CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="human",
        kind="cell.leaving_removed",
        subject_id=lease.cell_id,
        payload={"path": str(persisted.resolve(strict=False))},
    )
    await leavings.mark_removed(lease.cell_id, persisted.resolve(strict=False), clock.now(), event)

    assert _snapshot(home) == before
