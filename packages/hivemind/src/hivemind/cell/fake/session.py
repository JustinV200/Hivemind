"""Provide FakeSession, an in-memory CellSession for tests, demos and `hive doctor`.

`FakeSession` implements `hivemind.cell.session.CellSession` entirely in memory: `put_file`/
`get_file` read and write a plain dict keyed by resolved path instead of a real filesystem, and
`exec` is scripted rather than run through a real shell -- either by a `responder` callable that
computes a `CompletedCommand` from the `ExecSpec`, or by a mapping keyed on `argv[0]` for the
common "this command means this outcome" case, falling back to exit code 127 ("command not found")
for anything neither one recognises. A `slow_commands` set simulates a command that never returns,
so a caller can test its own timeout handling without an `asyncio.timeout` in this fake actually
elapsing. Shipped code, not test-only (codingrules section 14.4: "fakes live in src/ beside their
Protocol"), because `pollen`, `hive doctor` and demo paths use it too.

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
    - `close()` is idempotent; every method after it raises SessionClosedError.

See Also:
    - .claude/codingrules.md section 14.4 for "fakes live in src/, are shipped code."
    - hivemind.cell.session for the CellSession protocol, ExecSpec, ExecEvent and
      resolve_scratch_path, all of which this module implements or reuses.
    - hivemind.cell.fake.source for FakeCellSource, the RealCellSource that hands out FakeSessions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path

from hivemind.cell.errors import CommandTimeoutError, SessionClosedError
from hivemind.cell.session import (
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

Responder = Callable[[ExecSpec], CompletedCommand]

__all__ = ["FakeSession", "Responder"]


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

    async def close(self) -> None:
        """Close this session. Idempotent."""
        self._open = False

    def _resolve(self, spec: ExecSpec) -> CompletedCommand:
        """Compute a CompletedCommand for `spec` from the responder or mapping, or 127."""
        if callable(self._responder):
            return self._responder(spec)
        if isinstance(self._responder, Mapping):
            found = self._responder.get(spec.argv[0])
            if found is not None:
                return found
        return _COMMAND_NOT_FOUND
