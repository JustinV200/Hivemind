"""Provide FakeSession, an in-memory CellSession for tests, demos and `hive doctor`.

`FakeSession` implements `hivemind.cell.session.CellSession` entirely in memory: `put_file`/
`get_file` read and write a plain dict keyed by resolved path instead of a real filesystem, and
`exec` is scripted rather than run through a real shell -- either by a `responder` callable that
computes a `CompletedCommand` from the `ExecSpec`, or by a mapping keyed on `argv[0]` for the
common "this command means this outcome" case, falling back to exit code 127 ("command not found")
for anything neither one recognises. A `slow_commands` set simulates a command that never returns,
so a caller can test its own timeout handling without an `asyncio.timeout` in this fake actually
elapsing. Background processes (`start`, `stop`, `is_running`, roadmap step 6.4) are simulated
too: every program starts "running" with an empty log unless `script_start` says otherwise (what
its log file holds, that it exits at once, or that it cannot start at all), and `exit_process`
lets a test kill one out from under its caller the way a crash would. Shipped code, not test-only
(codingrules section 14.4: "fakes live in src/ beside their Protocol"), because `pollen`, `hive
doctor` and demo paths use it too.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implements `hivemind.cell.session.
    CellSession`; constructed by `hivemind.cell.fake.source.FakeCellSource.open_session` and
    directly by tests. Calls into hivemind.cell.errors and hivemind.cell.session only.

Key invariants:
    - `exec` is an async generator (codingrules: nothing runs until a caller starts iterating
      it), so a closed session or a slow command only raises once a caller actually consumes it.
    - `put_file`/`get_file` share `resolve_scratch_path` with `hivemind.cell.local.
      LocalProcessSession`, so both flavors enforce the identical scratch/allowed-paths rule the
      contract suite checks.
    - `close()` is idempotent; every method after it raises SessionClosedError, except `stop`
      and `is_running`, which (as for every CellSession) keep answering: nothing runs any more.
    - A simulated background pid is never reused within one session, starting at
      `FAKE_PID_BASE`, so a test can tell processes apart.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.cell.session for the CellSession protocol, ExecSpec, ExecEvent and
      resolve_scratch_path, all of which this module implements or reuses.
    - hivemind.cell.fake.source for FakeCellSource, the RealCellSource that hands out FakeSessions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell.errors import BackgroundStartError, CommandTimeoutError, SessionClosedError
from hivemind.cell.session import (
    BackgroundProcess,
    BackgroundSpec,
    CompletedCommand,
    ExecEvent,
    ExecSpec,
    ExitStatus,
    OutputChunk,
    OutputStream,
    resolve_scratch_path,
)
from waggle.clock import Clock

# A missing responder/mapping entry answers with this: a shell would report the same thing for an
# unknown command, so a script that forgot to stub something fails the same way it would for real.
_COMMAND_NOT_FOUND = CompletedCommand(
    exit_code=127, stdout=b"", stderr=b"command not found", duration_s=0.0
)

FAKE_PID_BASE = 40_000  # Simulated background pids count up from here, never reused per session.

Responder = Callable[[ExecSpec], CompletedCommand]

__all__ = ["FAKE_PID_BASE", "FakeSession", "FakeStart", "Responder"]


@dataclass(frozen=True, slots=True)
class FakeStart:
    """How FakeSession answers `start` for one program (keyed on `argv[0]`).

    Attributes:
        log: The bytes the program's log file holds once started (Xvfb's display number, say).
        running: False simulates a program that exits the moment it starts.
        fails: A reason to refuse the start outright with BackgroundStartError; None starts it.
    """

    log: bytes = b""
    running: bool = True
    fails: str | None = None


class FakeSession:
    """An in-memory CellSession: scripted exec, a files dict instead of a real filesystem."""

    def __init__(
        self,
        scratch_dir: Path,
        clock: Clock,
        responder: Responder | Mapping[str, CompletedCommand] | None = None,
        allowed_paths: tuple[Path, ...] = (),
        slow_commands: frozenset[str] = frozenset(),
    ) -> None:
        """Build a FakeSession scoped to `scratch_dir`.

        Args:
            scratch_dir: This session's scratch directory; every relative path resolves here.
            clock: Used only to timestamp ExitStatus.duration_s at a fixed 0.0 (the fake never
                actually waits); kept for signature parity with LocalProcessSession.
            responder: Either a callable computing a CompletedCommand from the ExecSpec, or a
                mapping from `argv[0]` to a fixed CompletedCommand. None answers every command
                with `_COMMAND_NOT_FOUND`.
            allowed_paths: Extra paths outside scratch this session may also touch.
            slow_commands: `argv[0]` values that raise CommandTimeoutError instead of answering,
                simulating a command that never returns.
        """
        self._scratch_dir = scratch_dir
        self._clock = clock
        self._responder = responder
        self._allowed_paths = allowed_paths
        self._slow_commands = slow_commands
        self._files: dict[Path, bytes] = {}
        self._open = True
        # Background processes (roadmap step 6.4) are simulated by their own small table.
        self._background = _BackgroundSimulation()

    def allow_path(self, path: Path) -> None:
        """Widen this session's reachable paths to `path`, as a lease widened after construction.

        The real sessions read their lease's `allowed_paths` live on every call, because
        `hivemind.wardens.spawn` widens the lease per sub-bee (roadmap step 5.0e's `keep_root`
        and declared Leavings) after the session exists; this is the fake's equivalent, so the
        CellSession contract can pin that behaviour across every implementation.
        """
        self._allowed_paths = (*self._allowed_paths, path.resolve(strict=False))

    def script_start(self, program: str, outcome: FakeStart) -> None:
        """Answer every later `start` of `program` (its `argv[0]`) with `outcome`."""
        self._background.scripts[program] = outcome

    def exit_process(self, process: BackgroundProcess) -> None:
        """Make a started process exit on its own, the way a crash would."""
        self._background.exit(process.pid)

    @property
    def started(self) -> tuple[BackgroundSpec, ...]:
        """Every BackgroundSpec `start` accepted, in order."""
        return tuple(self._background.started)

    @property
    def running_pids(self) -> tuple[int, ...]:
        """The simulated pids still running: started, and neither stopped nor exited."""
        return self._background.running_pids()

    @property
    def scratch_dir(self) -> Path:
        """This session's scratch directory."""
        return self._scratch_dir

    @property
    def is_open(self) -> bool:
        """Whether this session still accepts calls."""
        return self._open

    async def exec(self, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
        """Answer `spec` from the scripted responder/mapping, or simulate a timeout.

        Args:
            spec: The command to run.

        Yields:
            An OutputChunk per non-empty stream the scripted CompletedCommand carries, then one
            ExitStatus.

        Raises:
            SessionClosedError: This session is closed.
            CommandTimeoutError: `spec.argv[0]` is in `slow_commands`.
        """
        if not self._open:
            raise SessionClosedError(self.scratch_dir)
        if spec.argv[0] in self._slow_commands:
            raise CommandTimeoutError(spec.argv, spec.timeout_s)
        completed = self._resolve(spec)
        if completed.stdout:
            yield OutputChunk(stream=OutputStream.STDOUT, data=completed.stdout)
        if completed.stderr:
            yield OutputChunk(stream=OutputStream.STDERR, data=completed.stderr)
        yield ExitStatus(code=completed.exit_code, duration_s=completed.duration_s)

    async def put_file(self, path: Path, data: bytes) -> None:
        """Write `data` into the in-memory files dict, keyed by the resolved path.

        Args:
            path: Where to write; relative to `scratch_dir` when relative.
            data: The bytes to store.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `path` resolves outside scratch and every allowed path.
        """
        if not self._open:
            raise SessionClosedError(self.scratch_dir)
        resolved = resolve_scratch_path(self._scratch_dir, path, self._allowed_paths)
        self._files[resolved] = data

    async def get_file(self, path: Path) -> bytes:
        """Read the bytes stored at `path`'s resolved location.

        Args:
            path: Where to read from; relative to `scratch_dir` when relative.

        Returns:
            The stored bytes.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `path` resolves outside scratch and every allowed path.
            FileNotFoundError: No `put_file` ever wrote to this resolved path.
        """
        if not self._open:
            raise SessionClosedError(self.scratch_dir)
        resolved = resolve_scratch_path(self._scratch_dir, path, self._allowed_paths)
        if resolved not in self._files:
            raise FileNotFoundError(f"No file at {resolved} in this fake session.")
        return self._files[resolved]

    async def delete_file(self, path: Path) -> None:
        """Remove `path`'s resolved entry from the in-memory files dict.

        Args:
            path: What to remove; relative to `scratch_dir` when relative.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `path` resolves outside scratch and every allowed path.
            FileNotFoundError: No `put_file` ever wrote to this resolved path.
        """
        if not self._open:
            raise SessionClosedError(self.scratch_dir)
        resolved = resolve_scratch_path(self._scratch_dir, path, self._allowed_paths)
        if resolved not in self._files:
            raise FileNotFoundError(f"No file at {resolved} in this fake session.")
        del self._files[resolved]

    async def start(self, spec: BackgroundSpec) -> BackgroundProcess:
        """Simulate starting `spec`, answering from `script_start` or starting it running.

        Args:
            spec: The command to start.

        Returns:
            The simulated process, with a fresh pid.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `spec.log_path` resolves outside scratch.
            BackgroundStartError: `script_start` scripted this program to fail.
        """
        if not self._open:
            raise SessionClosedError(self.scratch_dir)
        return self._background.start(spec, self._scratch_dir, self._files)

    async def stop(self, process: BackgroundProcess) -> bool:
        """Mark `process` stopped; True when it was still running. Idempotent."""
        return self._background.running.pop(process.pid, False)

    async def is_running(self, process: BackgroundProcess) -> bool:
        """Return whether `process` was started here and is still running."""
        return self._background.running.get(process.pid, False)

    async def close(self) -> None:
        """Close this session, stopping every simulated background process. Idempotent."""
        self._open = False
        self._background.running.clear()

    def _resolve(self, spec: ExecSpec) -> CompletedCommand:
        """Compute a CompletedCommand for `spec` from the responder or mapping, or 127."""
        if callable(self._responder):
            return self._responder(spec)
        if isinstance(self._responder, Mapping):
            found = self._responder.get(spec.argv[0])
            if found is not None:
                return found
        return _COMMAND_NOT_FOUND


class _BackgroundSimulation:
    """FakeSession's simulated background processes: scripts, what started, and what still runs.

    Owns mutable state (codingrules 8.5, documented): `scripts` is written by
    `FakeSession.script_start`; `started` and `running` grow with every simulated `start`, and
    `running` shrinks as processes are stopped, exit or the session closes.
    """

    def __init__(self) -> None:
        """Build an empty simulation: nothing scripted, nothing started."""
        self.scripts: dict[str, FakeStart] = {}
        self.started: list[BackgroundSpec] = []
        self.running: dict[int, bool] = {}

    def start(
        self, spec: BackgroundSpec, scratch_dir: Path, files: dict[Path, bytes]
    ) -> BackgroundProcess:
        """Accept one start as scripted, writing its log into `files`, and return the process.

        Raises:
            PathNotAllowedError: `spec.log_path` resolves outside `scratch_dir`.
            BackgroundStartError: `spec.argv[0]` is scripted to fail.
        """
        outcome = self.scripts.get(spec.argv[0], FakeStart())
        if outcome.fails is not None:
            raise BackgroundStartError(spec.argv, outcome.fails)
        # A log path always lands inside scratch, as for every real session.
        log_path = None
        if spec.log_path is not None:
            log_path = resolve_scratch_path(scratch_dir, spec.log_path)
            files[log_path] = outcome.log
        pid = FAKE_PID_BASE + len(self.started)
        self.started.append(spec)
        self.running[pid] = outcome.running
        return BackgroundProcess(pid=pid, argv=spec.argv, log_path=log_path)

    def exit(self, pid: int) -> None:
        """Mark `pid` exited on its own, if it was started here."""
        if pid in self.running:
            self.running[pid] = False

    def running_pids(self) -> tuple[int, ...]:
        """Return the pids started, and neither stopped nor exited."""
        return tuple(pid for pid, is_up in self.running.items() if is_up)
