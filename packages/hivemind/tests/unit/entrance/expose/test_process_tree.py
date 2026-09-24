"""Tests for hivemind.entrance.expose.process_tree.kill_process_tree: no descendant outlives it.

A real child that starts a real grandchild: the Windows path of the tunnel supervisor depends on
this walk, and psutil walks the same way on every platform, so it is proved here on any host.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/process_tree.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.process_tree for the function under test.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil

from hivemind.entrance.expose.process_tree import kill_process_tree

_REAL_WAIT_S = 20.0  # A child interpreter starts in tens of milliseconds; this is a hang.
# A child that starts a sleeping grandchild, writes the grandchild's pid, then sleeps itself.
_PARENT = (
    "import subprocess, sys, time\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])\n"
    "open(sys.argv[1], 'w').write(str(grandchild.pid))\n"
    "time.sleep(3600)\n"
)


def _wait_for_pid(path: Path) -> int:
    """Poll (bounded, real time) for the pid the child writes once its grandchild runs."""
    deadline = time.monotonic() + _REAL_WAIT_S
    while not (path.exists() and path.read_text()):
        assert time.monotonic() < deadline, "The child never started its grandchild."
        time.sleep(0.01)
    return int(path.read_text())


def _gone(pid: int) -> bool:
    """True once `pid` has exited (a zombie awaiting its parent's reap counts as gone)."""
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_the_child_and_the_process_it_started_both_end(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    # SAFETY: a fixed argv running this test's own interpreter; no shell, no outside input.
    child = subprocess.Popen([sys.executable, "-c", _PARENT, str(pid_file)])  # noqa: S603
    grandchild = _wait_for_pid(pid_file)

    kill_process_tree(child.pid)
    child.wait(timeout=_REAL_WAIT_S)

    assert child.returncode is not None
    deadline = time.monotonic() + _REAL_WAIT_S
    while not _gone(grandchild):
        assert time.monotonic() < deadline, "The grandchild outlived the tree kill."
        time.sleep(0.01)


def test_a_tree_that_already_exited_is_left_alone() -> None:
    # Above every platform's pid ceiling (Linux caps pids at 2**22): no such process can exist.
    missing_pid = 2**31 - 1

    kill_process_tree(missing_pid)  # Returns quietly: a vanished tree is already ended.
