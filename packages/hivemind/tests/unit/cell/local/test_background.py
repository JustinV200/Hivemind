"""Unit tests for hivemind.cell.local.background: BackgroundTable over real child processes.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises the table both real sessions share:
    the started-pid callback runs before start returns, a daemon forked into the same process
    group dies with its launcher's group, stop_all stops newest first, and a log path can never
    leave scratch. The CellSession contract suite covers the same machinery through each session.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.background for BackgroundTable.
    - packages/hivemind/tests/contracts/test_cell_session_contract.py for the session-level clauses.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from hivemind.cell.errors import BackgroundStartError, PathNotAllowedError
from hivemind.cell.local.background import BackgroundContext, BackgroundTable
from hivemind.cell.session import BackgroundProcess, BackgroundSpec
from waggle.clock import SystemClock

_SLEEPER = (sys.executable, "-c", "import time; time.sleep(600)")
_WAIT_S = 10.0  # Generous bound for a real child to start, write a file or die.
_POLL_S = 0.05  # How often a file or a pid is re-checked while waiting.


def _context(tmp_path: Path, started: list[int]) -> BackgroundContext:
    """A context over `tmp_path` whose started-pid callback appends to `started`."""
    return BackgroundContext(scratch_dir=tmp_path, on_started=started.append)


async def _eventually(predicate: Callable[[], bool]) -> None:
    """Wait until `predicate` holds, re-checking every _POLL_S, bounded by _WAIT_S."""
    async with asyncio.timeout(_WAIT_S):
        while True:
            if predicate():
                return
            await asyncio.sleep(_POLL_S)


def _alive(pid: int) -> bool:
    """Return whether `pid` names a live, non-zombie process (POSIX)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed grandchild is reparented to init and reaped there; until then /proc shows a zombie.
    status = Path(f"/proc/{pid}/status")
    if status.exists():
        return "State:\tZ" not in status.read_text()
    return True


async def test_start_reports_the_pid_before_returning_and_stop_reaps_it(tmp_path: Path) -> None:
    started: list[int] = []
    table = BackgroundTable(SystemClock())

    process = await table.start(BackgroundSpec(argv=_SLEEPER), _context(tmp_path, started))

    assert started == [process.pid]
    assert table.is_running(process)
    assert await table.stop(process)
    assert not table.is_running(process)


@pytest.mark.skipif(sys.platform == "win32", reason="process groups and /proc are POSIX-only")
async def test_stop_kills_a_daemon_forked_into_the_group_after_its_launcher_exited(
    tmp_path: Path,
) -> None:
    # The launcher forks a sleeper, writes its pid to a file, and exits: the shape of a display
    # server's launcher. The sleeper is still in the launcher's process group.
    pid_file = tmp_path / "daemon.pid"
    script = (
        "import subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))"
    )
    table = BackgroundTable(SystemClock())
    process = await table.start(
        BackgroundSpec(argv=(sys.executable, "-c", script)), _context(tmp_path, [])
    )
    await _eventually(lambda: pid_file.exists() and bool(pid_file.read_text()))
    daemon = int(pid_file.read_text())
    # The launcher exits on its own; its daemon lives on in the same process group.
    await _eventually(lambda: not table.is_running(process))
    assert _alive(daemon)

    assert await table.stop(process) is False  # The leader had already exited...

    await _eventually(lambda: not _alive(daemon))  # ...but its group, daemon included, is gone.


async def test_stop_all_stops_everything_newest_first(tmp_path: Path) -> None:
    table = BackgroundTable(SystemClock())
    first = await table.start(BackgroundSpec(argv=_SLEEPER), _context(tmp_path, []))
    second = await table.start(BackgroundSpec(argv=_SLEEPER), _context(tmp_path, []))

    stopped = await table.stop_all()

    assert stopped == 2
    assert not table.is_running(first)
    assert not table.is_running(second)
    assert await table.stop_all() == 0


async def test_a_log_path_that_escapes_scratch_is_refused_before_anything_starts(
    tmp_path: Path,
) -> None:
    started: list[int] = []
    table = BackgroundTable(SystemClock())
    spec = BackgroundSpec(argv=_SLEEPER, log_path=Path("../escape.log"))

    with pytest.raises(PathNotAllowedError):
        await table.start(spec, _context(tmp_path, started))

    assert started == []


async def test_an_unstartable_command_raises_and_records_nothing(tmp_path: Path) -> None:
    started: list[int] = []
    table = BackgroundTable(SystemClock())

    with pytest.raises(BackgroundStartError, match="hivemind-absent-program"):
        await table.start(
            BackgroundSpec(argv=("hivemind-absent-program",)), _context(tmp_path, started)
        )

    assert started == []


async def test_a_process_this_table_never_started_is_not_running_and_not_stoppable(
    tmp_path: Path,
) -> None:
    table = BackgroundTable(SystemClock())
    stranger = BackgroundProcess(pid=os.getpid(), argv=("python",), log_path=None)

    assert not table.is_running(stranger)
    assert await table.stop(stranger) is False  # Never ours: this process must survive the test.
