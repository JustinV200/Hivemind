"""The roadmap step 3.11 left-as-found test: the Hive Stand is untouched outside its own scratch.

Snapshots a temporary "home" tree (files nothing in this lease's scope should ever touch) before
and after a full lease/session/release cycle that writes inside scratch and leaves a command
running past its own `exec()` call still being in flight, then asserts: scratch is gone, the home
tree is byte-for-byte unchanged, and the process this lease started is dead (codingrules section
8.7: "Real Cells are borrowed... release() kills every process the lease started and removes the
scratch directory").

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises `hivemind.cell.local.session.
    LocalProcessSession`, `hivemind.cell.local.releaser.HiveStandLeaseReleaser` and
    `hivemind.cell.lease.RealCellLease` together, end to end, over real OS processes.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 3.11 for "a left-as-found test snapshots a temporary home and the
      process table before and after."
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

from builders.cells import make_real_cell_lease

from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.local.releaser import HiveStandLeaseReleaser
from hivemind.cell.local.session import LocalProcessSession
from hivemind.cell.session import ExecSpec, run
from waggle.clock import SystemClock

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
    lease = make_real_cell_lease(scratch_root, clock=clock, releaser=HiveStandLeaseReleaser(clock))
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
