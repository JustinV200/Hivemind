"""Provide ObserverSession: a CellSession that runs a command to look at a machine, writing nothing.

A Warden (the per-Cell supervisor) with no bees working watches its Cell (ADR-0054), and where it
runs beside the Cell it looks through a session: a fixed read-only command that reads kernel tables
and file listings (`hivemind.wardens.watch.probe`). The Hive Stand's Warden holds one lease for its
whole life and lends the probe that lease's session. A colonized device's Warden does not: it gives
its lease back the moment it has nothing to supervise, so that the machine keeps no scratch between
tasks, and a lease opened for every look would put an open and a release on the trail every few
minutes for as long as the device is idle. `ObserverSession` is the session such a Warden watches
through instead. It belongs to no lease, so nothing it runs is recorded as a lease's process and
nothing is left to give back: its commands are children of the Warden that die with their own
timeout, and a run that is cancelled kills the whole tree it started (`run_child_process`). It can
run a command and do nothing else: every call that would write, read a file or leave a program
running is refused, and it has no scratch directory of its own to fill, only the directory it is
given to stand in and to ask the file system about (`df` reports on the disk that holds it).

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Implements
    `hivemind.cell.session.CellSession`; built by the composition root of a colonized device's
    Warden (`hivemind.cli.in_cell.borrowed.deps`) and held by its watch probe only. Calls into
    `hivemind.cell.errors`, `hivemind.cell.local.process` (the shared child-process machinery) and
    `hivemind.cell.session` only.

Key invariants:
    - `exec` runs what it is given and holds no lease, no quota and no record of the pids it
      started; the caller is the one thing that decides what to run (the watch probe's fixed
      scripts), and the session is handed to nothing else.
    - `put_file`, `get_file`, `delete_file` and `start` always refuse: no path is allowed and no
      background process may be left running. `stop` and `is_running` find nothing to act on.
    - A closed session refuses `exec`; closing needs nothing to be released, and is idempotent.
    - `scratch_dir` is the directory it was given, which is never created or written to here.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for watch mode.
    - hivemind.cell.local.session for the lease-bound session the Hive Stand's Warden looks through.
    - hivemind.wardens.watch.probe.session for the probe that holds it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from hivemind.cell.errors import BackgroundStartError, PathNotAllowedError, SessionClosedError
from hivemind.cell.local.process import ProcessContext, run_child_process
from hivemind.cell.session import BackgroundProcess, BackgroundSpec, ExecEvent, ExecSpec
from waggle.clock import Clock

__all__ = ["ObserverSession"]


class ObserverSession:
    """A CellSession over this machine that can run a command and nothing else."""

    def __init__(self, directory: Path, clock: Clock) -> None:
        """Build the session, standing in `directory`.

        Args:
            directory: An existing directory on the disk to be observed, such as the Warden's own
                state directory; commands start there and `df` reports on its file system. It is
                never written to by this class.
            clock: Source of every duration and timeout; never `datetime.now()`.
        """
        self._directory = directory
        self._clock = clock
        self._open = True

    @property
    def scratch_dir(self) -> Path:
        """The directory commands start in; named scratch by the protocol, never written here."""
        return self._directory

    @property
    def is_open(self) -> bool:
        """Whether this session still accepts `exec`."""
        return self._open

    async def exec(self, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
        """Run `spec` as a real child process, streaming output and ending in one ExitStatus.

        Args:
            spec: The command to run.

        Yields:
            OutputChunk as output arrives across both streams, then one ExitStatus; a command that
            cannot start is reported as exit 127 with the reason on stderr, never as an OSError.

        Raises:
            SessionClosedError: This session is closed.
            CommandTimeoutError: `spec` did not finish within `spec.timeout_s`; its process tree
                has been killed.
        """
        if not self._open:
            raise SessionClosedError(self._directory)
        # No quota (nothing here writes, so there is no scratch to cap) and no lease to tell about
        # the pid: the run kills its own tree on a timeout or a cancel.
        context = ProcessContext(
            clock=self._clock, scratch_dir=self._directory, quota=None, on_started=_forget_pid
        )
        async for event in run_child_process(spec, context):
            yield event

    async def put_file(self, path: Path, data: bytes) -> None:
        """Refuse: an observer writes nothing.

        Raises:
            PathNotAllowedError: Always; no path is one this session may touch.
        """
        raise PathNotAllowedError(path)

    async def get_file(self, path: Path) -> bytes:
        """Refuse: an observer reads through a command, never a file of its own.

        Raises:
            PathNotAllowedError: Always.
        """
        raise PathNotAllowedError(path)

    async def delete_file(self, path: Path) -> None:
        """Refuse: an observer removes nothing.

        Raises:
            PathNotAllowedError: Always.
        """
        raise PathNotAllowedError(path)

    async def start(self, spec: BackgroundSpec) -> BackgroundProcess:
        """Refuse: an observer leaves nothing running.

        Raises:
            BackgroundStartError: Always.
        """
        raise BackgroundStartError(spec.argv, "an observer session only looks and starts nothing")

    async def stop(self, process: BackgroundProcess) -> bool:
        """Say that nothing was stopped: this session never started `process`."""
        return False

    async def is_running(self, process: BackgroundProcess) -> bool:
        """Say that nothing runs: this session never started `process`."""
        return False

    async def close(self) -> None:
        """Close the session. Idempotent; there is nothing to release."""
        self._open = False


def _forget_pid(pid: int) -> None:
    """Take a started pid and keep no record of it: the run itself kills what it started."""
