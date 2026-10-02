"""Tests for builders.orphan_browsers: which leftover browsers a test session's start stops.

Fits into the Hive:
    Test infrastructure, not shipped; beside `contracts.browser_harness`, whose real browsers are
    the ones `builders.orphan_browsers` cleans up after. Every case runs over a fake `/proc` built
    in `tmp_path` and records the signals instead of sending them, so it runs on every platform
    and never touches a real process.

Key invariants:
    - None: this module holds tests only.

See Also:
    - builders.orphan_browsers for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.orphan_browsers import (
    KILL_SIGNAL,
    orphaned_test_browsers,
    stop_orphaned_test_browsers,
)

_CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
_PROFILE = "--user-data-dir=/tmp/hm-abc123/browser/profile"
_PYTHON = "/work/.venv/bin/python3"


def _process(proc: Path, pid: int, ppid: int, *argv: str, state: str = "S") -> None:
    """Write one fake /proc/<pid> with the kernel's own cmdline and stat layouts."""
    entry = proc / str(pid)
    entry.mkdir(parents=True)
    (entry / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
    name = Path(argv[0]).name if argv else "kthread"
    (entry / "stat").write_text(f"{pid} ({name}) {state} {ppid} 1 1 0", encoding="utf-8")


def _table(proc: Path) -> Path:
    """A table with init, a live pytest and its browser, and the orphan of a killed run."""
    _process(proc, 1, 0, "/sbin/init")
    _process(proc, 100, 1, _PYTHON, "-m", "pytest")
    _process(proc, 101, 100, _CHROME, _PROFILE, "--headless=new")  # The live run's browser.
    _process(proc, 200, 1, _CHROME, "--user-data-dir=/tmp/hm-dead00/browser/profile")
    _process(proc, 201, 200, _CHROME, "--type=renderer", "--user-data-dir=/tmp/hm-dead00/b/p")
    return proc


def test_only_the_orphaned_harness_browser_and_its_children_are_found(tmp_path: Path) -> None:
    proc = _table(tmp_path / "proc")

    assert orphaned_test_browsers(proc) == (200, 201)


def test_a_browser_outside_harness_scratch_is_never_found(tmp_path: Path) -> None:
    # The operator's own Chromium, orphaned or not, names no /tmp/hm- scratch.
    proc = tmp_path / "proc"
    _process(proc, 1, 0, "/sbin/init")
    _process(proc, 300, 1, "/usr/bin/chromium", "--user-data-dir=/home/me/.config/chromium")

    assert orphaned_test_browsers(proc) == ()


def test_a_non_browser_naming_harness_scratch_is_never_found(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _process(proc, 1, 0, "/sbin/init")
    _process(proc, 400, 1, "/usr/bin/pulseaudio", "--runtime-dir=/tmp/hm-dead00/pulse")

    assert orphaned_test_browsers(proc) == ()


def test_a_zombie_or_an_unseen_parent_is_left_alone(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _process(proc, 1, 0, "/sbin/init")
    _process(proc, 500, 1, _CHROME, _PROFILE, state="Z")
    _process(proc, 501, 999, _CHROME, _PROFILE)  # Pid 999 is not in the table.

    assert orphaned_test_browsers(proc) == ()


def test_nothing_is_found_without_a_proc_table(tmp_path: Path) -> None:
    assert orphaned_test_browsers(tmp_path / "no-proc") == ()


def test_stopping_sends_sigkill_to_each_orphan_only(tmp_path: Path) -> None:
    proc = _table(tmp_path / "proc")
    sent: list[tuple[int, int]] = []

    stopped = stop_orphaned_test_browsers(proc, kill=lambda pid, sig: sent.append((pid, sig)))

    assert stopped == (200, 201)
    assert sent == [(200, KILL_SIGNAL), (201, KILL_SIGNAL)]


def test_stopping_passes_over_a_process_that_already_exited(tmp_path: Path) -> None:
    proc = _table(tmp_path / "proc")

    def _gone(pid: int, sig: int) -> None:
        """Answer as the kernel does for a pid that exited since it was read."""
        raise ProcessLookupError(pid)

    stopped = stop_orphaned_test_browsers(proc, kill=_gone)

    assert stopped == (200, 201)
