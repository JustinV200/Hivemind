"""Define the Sandbox protocol: the one place the Quarantine Comb runs untrusted tool code.

The Comb never touches a filesystem or starts a process itself; it asks a sandbox. A `Sandbox` is
a directory (its `root`) in an isolated place, with five verbs: put bytes under the root, run a
bounded command with the root as its working directory, read bytes back, and clean up. Two
implementations exist: `SandboxCell` (a Virtual Cell reached through its `CellSession`, the
production path) and `SandboxSubprocess` (a temporary directory and the local interpreter, for
development only). This module also holds what both share: the exit code a timed-out command
reports, a capped output collector, and the check that keeps a relative path inside the root.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Implemented by `sandbox_cell` and `sandbox_subprocess`; called by `QuarantineComb`. Calls into
    `hivemind.cell.session` for `CompletedCommand` only.

Key invariants:
    - `put` and `read` accept only relative paths that stay under `root`; anything else raises
      `SandboxPathError` before touching a filesystem.
    - `exec` never raises for a command that fails or runs too long: a timeout is the
      `TIMED_OUT_EXIT_CODE` result with the output so far, and the whole process group is killed.
    - Captured output is capped: bytes past the cap are dropped, never buffered.
    - `cleanup` is idempotent.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the two implementations and why.
    - hivemind.royal_jelly.quarantine_comb.sandbox_cell and .sandbox_subprocess.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Protocol

from hivemind.cell.session import CompletedCommand
from hivemind.royal_jelly.quarantine_comb.errors import SandboxPathError
from waggle.ids import CellId

TIMED_OUT_EXIT_CODE = 124  # The code coreutils `timeout` uses; a run killed for time reports it.
DEFAULT_OUTPUT_BYTES = (
    1024 * 1024
)  # QuarantineLimits' default output cap, for an unconfigured sandbox.

__all__ = [
    "DEFAULT_OUTPUT_BYTES",
    "TIMED_OUT_EXIT_CODE",
    "CappedOutput",
    "Sandbox",
    "check_relative",
]


class Sandbox(Protocol):
    """An isolated directory the Comb puts a package into and runs the runner in."""

    @property
    def root(self) -> Path:
        """The sandbox's working directory; every relative path resolves under it."""
        ...

    @property
    def cell_id(self) -> CellId | None:
        """The sandbox Virtual Cell's id; None for a sandbox that has no Cell."""
        ...

    @property
    def interpreter(self) -> str:
        """The command that starts Python in this sandbox (`argv[0]` of the runner)."""
        ...

    async def put(self, relative: Path, data: bytes) -> None:
        """Write `data` at `relative` under the root, creating parent directories.

        Args:
            relative: A relative path with no `..` segment.
            data: The bytes to write.

        Raises:
            SandboxPathError: `relative` is absolute or leaves the root.
        """
        ...

    async def exec(
        self, argv: tuple[str, ...], timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CompletedCommand:
        """Run `argv` with the root as its working directory and collect its output.

        Args:
            argv: The command and its arguments; never run through a shell.
            timeout_s: Wall seconds to allow before killing the command's process group.
            env: Extra environment variables for this command.

        Returns:
            The outcome. A command that ran too long has `exit_code` `TIMED_OUT_EXIT_CODE`.
        """
        ...

    async def read(self, relative: Path) -> bytes:
        """Read the bytes at `relative` under the root.

        Args:
            relative: A relative path with no `..` segment.

        Returns:
            The file's contents.

        Raises:
            SandboxPathError: `relative` is absolute or leaves the root.
            FileNotFoundError: No such file exists.
        """
        ...

    async def cleanup(self) -> None:
        """Remove everything the sandbox created. Idempotent; never raises for a missing root."""
        ...


def check_relative(relative: Path) -> Path:
    """Return `relative` unchanged if it is a plain relative path, else raise.

    Args:
        relative: The path a caller handed a sandbox.

    Returns:
        The same path, for chaining.

    Raises:
        SandboxPathError: The path is empty, absolute or holds a `..` segment.
    """
    # Checked lexically on the POSIX form so the rule is the same on every host; the sandbox
    # root is fresh, so there is no symlink in it for a lexical check to miss.
    posix = PurePosixPath(relative.as_posix())
    if not relative.parts or posix.is_absolute() or relative.is_absolute() or ".." in posix.parts:
        raise SandboxPathError(f"Sandbox path {str(relative)[:80]!r} must stay under the root.")
    return relative


class CappedOutput:
    """Collect a command's stdout and stderr up to one shared byte cap.

    Mutable on purpose and owned by one `exec` call: chunks arrive one at a time and the running
    total decides what is kept. Bytes past the cap are counted as dropped, never stored, so a
    tool that prints without end cannot grow the Warden's memory.
    """

    def __init__(self, cap: int) -> None:
        """Start an empty collector.

        Args:
            cap: The most bytes kept across both streams together.
        """
        self._cap = cap
        self._stdout = bytearray()
        self._stderr = bytearray()
        self.dropped = 0  # Bytes seen but not kept; non-zero means the cap was reached.

    def add(self, *, is_stderr: bool, data: bytes) -> None:
        """Keep as much of `data` as the cap still allows.

        Args:
            is_stderr: Whether the chunk came from stderr rather than stdout.
            data: The chunk.
        """
        room = max(0, self._cap - len(self._stdout) - len(self._stderr))
        kept = data[:room]
        self.dropped += len(data) - len(kept)
        (self._stderr if is_stderr else self._stdout).extend(kept)

    def result(self, exit_code: int, duration_s: float) -> CompletedCommand:
        """Build the `CompletedCommand` for what was collected.

        Args:
            exit_code: The command's exit code.
            duration_s: Wall seconds it ran.

        Returns:
            The collected outcome.
        """
        return CompletedCommand(
            exit_code=exit_code,
            stdout=bytes(self._stdout),
            stderr=bytes(self._stderr),
            duration_s=max(0.0, duration_s),
        )
