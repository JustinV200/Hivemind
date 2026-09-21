"""Unit tests for hivemind.cell.in_cell: InCellSession's own specifics (roadmap step 5.5).

The `CellSession` contract itself (exec, put_file/get_file/delete_file, close) is proven by
`packages/hivemind/tests/contracts/test_cell_session_contract.py`'s `"in_cell"` harness; this
module tests what makes `InCellSession`/`InCellLeaseReleaser` different from
`LocalProcessSession`/`HiveStandLeaseReleaser`: no scratch quota watchdog, and release() never
restores a path or removes scratch because the whole Virtual Cell is disposable.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises `hivemind.cell.in_cell.InCellSession`
    and `.InCellLeaseReleaser` directly, over real OS processes and a real filesystem.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 5.5 for "no borrowed-host lease/restore because the whole Cell is
      disposable... destroy is the cleanup."
    - hivemind.cell.in_cell for the module under test.
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

from hivemind.cell.in_cell import InCellLeaseReleaser, InCellSession
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.session import ExecSpec, run
from waggle.clock import SystemClock

_WAIT_TIMEOUT_S = 5.0  # Generous bound for a process this test starts to register or die.
_LONG_SLEEP_S = 60.0  # Long enough that it is still running when release() reaches for it.


async def test_release_never_removes_scratch_or_restores_a_path(tmp_path: Path) -> None:
    # A Capping-approved outside-scratch write would normally record a RestoreRecord
    # (RealCellLease.note_restore_path); InCellLeaseReleaser must skip it entirely, because
    # nothing outside this disposable Cell could ever read the restored file back.
    outside = tmp_path.parent / "outside-in-cell-test"
    outside.mkdir(exist_ok=True)
    target = outside / "touched.txt"
    target.write_text("original")
    clock = SystemClock()
    lease = make_real_cell_lease(
        tmp_path, clock=clock, releaser=InCellLeaseReleaser(clock), allowed_paths=(outside,)
    )
    await lease.open()
    lease.note_restore_path(target, prior=b"original")
    target.write_text("overwritten")

    report = await lease.release()

    assert report.is_restored is True
    assert report.residual_paths == ()
    # Scratch itself is left in place; InCellLeaseReleaser never removes it, and the touched
    # path outside scratch is never restored: destroy is the cleanup, not this.
    assert lease.scratch_root.exists()
    assert target.read_text() == "overwritten"


async def test_release_kills_a_still_running_process_the_session_started(tmp_path: Path) -> None:
    clock = SystemClock()
    lease = make_real_cell_lease(tmp_path, clock=clock, releaser=InCellLeaseReleaser(clock))
    await lease.open()
    session = InCellSession(lease, clock)
    # A background task, not awaited here: release() must reach a command while it is still
    # running, mirroring test_left_as_found.py's own "release() races a still-open exec()" setup.
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

    report = await lease.release()
    await asyncio.wait_for(linger, timeout=_WAIT_TIMEOUT_S)

    assert report.killed_processes == 1
    assert not _is_process_alive(pid)


async def test_no_quota_watchdog_lets_a_large_write_through(tmp_path: Path) -> None:
    clock = SystemClock()
    lease = make_real_cell_lease(tmp_path, clock=clock)
    session = InCellSession(lease, clock)
    payload = b"y" * (256 * 1024)  # Comfortably larger than any test-sized quota would allow.
    # Fed over stdin, not embedded in argv: a payload this size blows past the OS command-line
    # length limit if it were spelled out as a Python literal instead.
    spec = ExecSpec(
        argv=(sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        stdin=payload,
    )

    completed = await run(session, spec)

    assert completed.exit_code == 0
    assert completed.stdout == payload


async def test_lease_state_reaches_released_after_release(tmp_path: Path) -> None:
    clock = SystemClock()
    lease = make_real_cell_lease(tmp_path, clock=clock, releaser=InCellLeaseReleaser(clock))
    await lease.open()

    await lease.release()

    assert lease.state is LeaseState.RELEASED


async def _wait_until(predicate: Callable[[], bool], timeout_s: float) -> None:
    """Poll `predicate` until it is True, or raise once `timeout_s` real seconds have passed.

    A real process's own startup time cannot be simulated with a FakeClock (codingrules 14.5's
    "no sleeping in tests" is for logical waits; this test coordinates with a real subprocess).
    """
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout_s}s")
        await asyncio.sleep(0.01)


def _is_process_alive(pid: int) -> bool:
    """Return whether `pid` is still a live process, POSIX and Windows shims side by side.

    Mirrors `test_left_as_found.py`'s own helper exactly: a `tasklist` query on Windows (an
    `OpenProcess` handle check was tried first and found to report a just-killed pid alive for a
    beat past `taskkill`'s own return, the same lingering-handle race `HiveStandLeaseReleaser`'s
    own `_SCRATCH_REMOVE_ATTEMPTS` retry documents), `os.kill(pid, 0)` elsewhere.
    """
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
            return True  # Exists, owned by someone else -- cannot happen for our own child.
        return True
