"""Define CellSession: the terminal every Cell offers, and the events an exec streams back.

codingrules section 8.7: "A session is a terminal." Whatever kind a Cell is (Real or Virtual) and
wherever it runs, the one way a Worker or a tool runs a command or touches a file on it is a
`CellSession`: `exec` (streaming output), `put_file`, `get_file`, a `scratch_dir` every relative
path resolves against, and `close`. This module also defines the small value types an `exec` call
streams (`OutputChunk`, `ExitStatus`, `ExecEvent`), the request it takes (`ExecSpec`), and `run`, a
convenience that drives a session's `exec` to completion and collects it into one `CompletedCommand`
for a caller that does not need to react to output as it arrives.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implemented by `hivemind.cell.fake.
    FakeSession` and `hivemind.cell.local.LocalProcessSession` (phase 3 step 3.11); a later phase
    adds `InCellSession` (inside a Virtual Cell) and `PollenSession` (a Swarm device over Waggle).
    Called by every Worker tool that runs a command or touches a file (`hivemind.workers.tools`).
    Calls into hivemind.cell.errors only.

Key invariants:
    - `exec` is an async generator: the last `ExecEvent` it yields is always an `ExitStatus`, or
      the generator raises `CommandTimeoutError` or `SessionClosedError` instead of yielding one.
    - `ExecSpec.cwd`, `put_file`'s `path` and `get_file`'s `path` all resolve relative to
      `scratch_dir` when given as a relative path; an implementation resolves `..` and symlinks
      with `Path.resolve(strict=False)` before checking a path is in reach (`resolve_scratch_path`
      below is the one place that check lives, shared by every concrete session).
    - `close` is idempotent and kills whatever `exec` started on this session.

See Also:
    - .claude/codingrules.md section 8.7 for "A session is a terminal."
    - .claude/codingrules.md Appendix A.1 for this module's Protocol-and-implementation shape.
    - hivemind.cell.errors for SessionClosedError, CommandTimeoutError and PathNotAllowedError.
    - hivemind.cell.lease for RealCellLease, which a session's implementation reports started
      processes and touched paths back to.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell.errors import PathNotAllowedError
from hivemind.common.errors import InvariantViolationError

DEFAULT_EXEC_TIMEOUT_S = 60.0  # Generous for a one-off shell command; long tools set their own.

__all__ = [
    "DEFAULT_EXEC_TIMEOUT_S",
    "CellSession",
    "CompletedCommand",
    "ExecEvent",
    "ExecSpec",
    "ExitStatus",
    "OutputChunk",
    "OutputStream",
    "resolve_scratch_path",
    "run",
]


class OutputStream(Enum):
    """Which stream one OutputChunk of a running command's output came from."""

    STDOUT = "STDOUT"
    STDERR = "STDERR"


class OutputChunk(BaseModel):
    """One arrived slice of a running command's output, on one stream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stream: OutputStream = Field(description="Which stream this chunk came from.")
    data: bytes = Field(description="The bytes that arrived; never decoded or line-split here.")


class ExitStatus(BaseModel):
    """The final event of an exec call: the command's exit code and how long it ran."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: int = Field(description="The process exit code (platform-defined for a signal death).")
    duration_s: float = Field(ge=0, description="Wall-clock seconds from start to exit.")


ExecEvent = OutputChunk | ExitStatus  # Everything session.exec streams; the last is an ExitStatus.


class ExecSpec(BaseModel):
    """One command to run on a CellSession, with its working directory, env, timeout and stdin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    argv: tuple[str, ...] = Field(
        min_length=1, description="The command and its arguments; never run through a shell."
    )
    cwd: Path | None = Field(
        default=None,
        description="Working directory; relative to scratch_dir when relative, None for scratch "
        "itself.",
    )
    env: dict[str, str] = Field(
        default_factory=dict, description="Extra environment variables, added to the session's."
    )
    timeout_s: float = Field(
        default=DEFAULT_EXEC_TIMEOUT_S, gt=0, description="Seconds to allow before killing it."
    )
    stdin: bytes | None = Field(default=None, description="Bytes to write to stdin, if any.")


class CellSession(Protocol):
    """A terminal session on one Cell: exec, put a file, get a file, close.

    The only way a Worker or a tool runs a command or touches a file on its Cell (codingrules
    section 4). Implementations must be safe to call `exec` on more than once concurrently.
    """

    @property
    def scratch_dir(self) -> Path:
        """This session's scratch directory; every relative path resolves against it."""
        ...

    @property
    def is_open(self) -> bool:
        """Whether this session still accepts exec/put_file/get_file calls."""
        ...

    def exec(self, spec: ExecSpec) -> AsyncIterator[ExecEvent]:
        """Run `spec` and stream its output, ending in exactly one ExitStatus.

        An async generator: nothing runs until the caller starts iterating it. The last event is
        always an ExitStatus, unless the generator raises first.

        Args:
            spec: The command to run.

        Yields:
            OutputChunk as output arrives, in the order it arrived across both streams, then one
            ExitStatus.

        Raises:
            SessionClosedError: This session is closed.
            CommandTimeoutError: `spec` did not finish within `spec.timeout_s`.
        """
        ...

    async def put_file(self, path: Path, data: bytes) -> None:
        """Write `data` to `path`, creating or overwriting it.

        Args:
            path: Where to write; relative to `scratch_dir` when relative.
            data: The bytes to write.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `path` resolves outside scratch and outside every path this
                session's lease allows touching.
        """
        ...

    async def get_file(self, path: Path) -> bytes:
        """Read and return the bytes at `path`.

        Args:
            path: Where to read from; relative to `scratch_dir` when relative.

        Returns:
            The file's full contents.

        Raises:
            SessionClosedError: This session is closed.
            PathNotAllowedError: `path` resolves outside scratch and outside every path this
                session's lease allows touching.
            FileNotFoundError: No such file exists.
        """
        ...

    async def close(self) -> None:
        """Close this session, killing whatever `exec` started on it. Idempotent."""
        ...


class CompletedCommand(BaseModel):
    """One command's outcome, collected from an exec call by `run`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exit_code: int = Field(description="The process exit code.")
    stdout: bytes = Field(description="Every stdout OutputChunk's data, concatenated in order.")
    stderr: bytes = Field(description="Every stderr OutputChunk's data, concatenated in order.")
    duration_s: float = Field(ge=0, description="Wall-clock seconds from start to exit.")


async def run(session: CellSession, spec: ExecSpec) -> CompletedCommand:
    """Drive `session.exec(spec)` to completion and collect it into one CompletedCommand.

    For a caller that only needs the final result, not each chunk as it arrives; a caller that
    wants to stream output (to a log, to a human) uses `session.exec` directly instead.

    Args:
        session: The session to run the command on.
        spec: The command to run.

    Returns:
        The collected outcome: exit code, full stdout, full stderr, duration.

    Raises:
        SessionClosedError: `session` is closed.
        CommandTimeoutError: `spec` did not finish within `spec.timeout_s`.
    """
    stdout = bytearray()
    stderr = bytearray()
    exit_status: ExitStatus | None = None
    # Every exec stream ends in exactly one ExitStatus (this module's own contract); everything
    # before that is output to accumulate onto the matching stream's buffer.
    async for event in session.exec(spec):
        if isinstance(event, OutputChunk):
            if event.stream is OutputStream.STDOUT:
                stdout.extend(event.data)
            else:
                stderr.extend(event.data)
        else:
            exit_status = event
    if exit_status is None:
        # A conforming CellSession never reaches this: exec's contract guarantees a final
        # ExitStatus or a raised error. Reaching it means an implementation broke that contract.
        raise InvariantViolationError(
            f"{session!r}.exec({spec!r}) ended without yielding a final ExitStatus."
        )
    return CompletedCommand(
        exit_code=exit_status.code,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        duration_s=exit_status.duration_s,
    )


def resolve_scratch_path(scratch_dir: Path, path: Path, allowed_paths: Sequence[Path] = ()) -> Path:
    """Resolve `path` against `scratch_dir` and check it is reachable from this session.

    Shared by every concrete CellSession's put_file/get_file so the resolve-then-check rule
    (codingrules section 15: paths are validated before they reach a filesystem or subprocess
    call) is written once. A relative `path` joins under `scratch_dir` first, matching
    `ExecSpec.cwd`'s own rule; the result is then resolved with `Path.resolve(strict=False)` --
    which collapses `..` segments and follows symlinks without requiring the target to exist --
    before being compared against `scratch_dir` and `allowed_paths`, so neither a `..` segment
    nor a symlink can be used to sneak outside scratch undetected.

    Args:
        scratch_dir: The session's scratch directory; every relative path resolves against it.
        path: The path a caller passed to put_file/get_file, relative or absolute.
        allowed_paths: Extra absolute paths, outside scratch, this session may also touch.

    Returns:
        The resolved, absolute Path, once confirmed reachable.

    Raises:
        PathNotAllowedError: The resolved path is neither inside `scratch_dir` nor under one of
            `allowed_paths`.
    """
    joined = path if path.is_absolute() else scratch_dir / path
    resolved = joined.resolve(strict=False)
    scratch_resolved = scratch_dir.resolve(strict=False)
    roots = (scratch_resolved, *(allowed.resolve(strict=False) for allowed in allowed_paths))
    if any(resolved == root or root in resolved.parents for root in roots):
        return resolved
    raise PathNotAllowedError(resolved)
