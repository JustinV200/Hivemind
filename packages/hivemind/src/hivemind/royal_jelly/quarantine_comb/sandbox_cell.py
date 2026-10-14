"""Provide SandboxCell: a sandbox that is a fresh directory inside a Virtual Cell's session.

The production sandbox. The Quarantine Comb runs inside a provisioned Virtual Cell (an isolated
container or VM the Hive creates and later destroys), driven by that Cell's own `CellSession` (the
terminal every Cell offers). `SandboxCell` wraps the Cell and its session: its root is a new
`comb-<id>` directory below the session's scratch directory, `put` and `read` go through the
session's file calls, and `exec` runs through the session with that root as the working directory.
It refuses a borrowed Cell at construction: the Comb runs untrusted generated code, and a Real
Cell (an existing device the Hive borrows and must leave exactly as found) can never host it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Built by the Warden of the sandbox Cell (ADR-0047) over its own session; used by
    `QuarantineComb`. Calls into `hivemind.cell` (Cell, CellSession) and waggle's clock and ids.

Key invariants:
    - Construction raises `SandboxRefusedError` for a borrowed Cell; this module asks
      `cell.is_borrowed` and never reads `cell.kind`.
    - Every path stays under `root`, which stays under the session's scratch directory.
    - The session is not closed here: it belongs to the Warden that lent it.
    - A command that outlives its timeout reports `TIMED_OUT_EXIT_CODE` with the output so far.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for why the Comb runs where its sandbox is.
    - hivemind.royal_jelly.quarantine_comb.sandbox for the protocol and the shared helpers.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Mapping
from pathlib import Path

from hivemind.cell.errors import CommandTimeoutError
from hivemind.cell.models import Cell
from hivemind.cell.session import (
    CellSession,
    CompletedCommand,
    ExecSpec,
    ExitStatus,
    OutputChunk,
    OutputStream,
)
from hivemind.royal_jelly.quarantine_comb.errors import SandboxRefusedError
from hivemind.royal_jelly.quarantine_comb.sandbox import (
    DEFAULT_OUTPUT_BYTES,
    TIMED_OUT_EXIT_CODE,
    CappedOutput,
    check_relative,
)
from waggle.clock import Clock
from waggle.ids import CellId

_ROOT_PREFIX = "comb-"  # Marks a Comb directory so a stray one is recognisable in scratch.
_MARKER = ".sandbox"  # Empty file whose only job is to make the root directory exist.
_ROOT_ENTROPY_BYTES = 6  # 48 random bits: unique among the few sandboxes one session ever hosts.
_CLEANUP_TIMEOUT_S = 30.0  # Removing a directory tree is quick; a stuck rm must not hang teardown.

__all__ = ["SandboxCell"]


class SandboxCell:
    """A sandbox rooted in a fresh directory of a Virtual Cell's session scratch."""

    def __init__(
        self,
        cell: Cell,
        session: CellSession,
        clock: Clock,
        *,
        output_bytes: int = DEFAULT_OUTPUT_BYTES,
        interpreter: str | None = None,
    ) -> None:
        """Build a sandbox over `cell` and its `session`.

        Args:
            cell: The Cell the session belongs to; must be provisioned, not borrowed.
            session: The Cell's terminal; its scratch directory holds the sandbox root.
            clock: Measures how long a command ran; never `datetime.now()`.
            output_bytes: Cap on the stdout and stderr one `exec` keeps (`QuarantineLimits`).
            interpreter: The command that starts Python in this Cell. None means the interpreter
                running this Warden (`sys.executable`): the Warden lives on the Cell it sandboxes,
                and its own interpreter is the one with the Hive installed, where a bare `python3`
                need not be (a Windows venv ships no `python3.exe`; an image's venv is off PATH).

        Raises:
            SandboxRefusedError: `cell` is borrowed (a Real Cell).
        """
        # A borrowed device is left exactly as found, so it can never host a stranger's code.
        if cell.is_borrowed:
            raise SandboxRefusedError(
                f"Cell {cell.id} is borrowed; a Real Cell can never be a Quarantine Comb sandbox."
            )
        self._cell = cell
        self._session = session
        self._clock = clock
        self._output_bytes = output_bytes
        self._interpreter = interpreter or sys.executable
        # Random rather than clock-derived: two sandboxes made in one tick must not collide.
        self._root = session.scratch_dir / f"{_ROOT_PREFIX}{secrets.token_hex(_ROOT_ENTROPY_BYTES)}"
        self._created = False  # Whether the root directory exists yet (the first write makes it).

    @property
    def root(self) -> Path:
        """The sandbox directory inside the session's scratch."""
        return self._root

    @property
    def cell_id(self) -> CellId | None:
        """The sandbox Virtual Cell's id."""
        return self._cell.id

    @property
    def interpreter(self) -> str:
        """The command that starts Python inside this Cell."""
        return self._interpreter

    async def put(self, relative: Path, data: bytes) -> None:
        """Write `data` under the root through the session (parents are created).

        Args:
            relative: A relative path with no `..` segment.
            data: The bytes to write.

        Raises:
            SandboxPathError: `relative` leaves the root.
        """
        await self._session.put_file(self._root / check_relative(relative), data)
        self._created = True

    async def read(self, relative: Path) -> bytes:
        """Read the file at `relative` under the root through the session.

        Args:
            relative: A relative path with no `..` segment.

        Returns:
            The file's contents.

        Raises:
            SandboxPathError: `relative` leaves the root.
            FileNotFoundError: No such file exists.
        """
        return await self._session.get_file(self._root / check_relative(relative))

    async def exec(
        self, argv: tuple[str, ...], timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CompletedCommand:
        """Run `argv` in the root through the session, capping the output it keeps.

        Args:
            argv: The command and its arguments.
            timeout_s: Wall seconds before the session kills it.
            env: Extra environment variables.

        Returns:
            The outcome; `TIMED_OUT_EXIT_CODE` when the session reports a timeout.
        """
        await self._ensure_root()
        spec = ExecSpec(argv=argv, cwd=self._root, env=dict(env or {}), timeout_s=timeout_s)
        output = CappedOutput(self._output_bytes)
        started = self._clock.monotonic()
        # The stream ends in one ExitStatus, or the session raises a timeout after killing the
        # command; either way what arrived so far is kept, so a hung tool's output still shows.
        try:
            async for event in self._session.exec(spec):
                if isinstance(event, OutputChunk):
                    output.add(is_stderr=event.stream is OutputStream.STDERR, data=event.data)
                elif isinstance(event, ExitStatus):
                    return output.result(event.code, event.duration_s)
        except CommandTimeoutError:
            return output.result(TIMED_OUT_EXIT_CODE, self._clock.monotonic() - started)
        return output.result(TIMED_OUT_EXIT_CODE, self._clock.monotonic() - started)

    async def _ensure_root(self) -> None:
        """Create the root directory if nothing has been put yet, so a command can run in it."""
        # The session has no mkdir verb, but put_file creates parents; an empty marker file is
        # the cheapest way to make the directory exist without starting a process.
        if not self._created:
            await self._session.put_file(self._root / _MARKER, b"")
            self._created = True

    async def cleanup(self) -> None:
        """Remove the sandbox directory with `rm -rf`; a failure is ignored."""
        # No session verb removes a directory, and the sandbox is an Ubuntu Cell by definition
        # (ADR-0047), so `rm` is there. The whole Cell is torn down by the Queen right after, so
        # a failed removal costs nothing and must not mask the report.
        spec = ExecSpec(argv=("rm", "-rf", str(self._root)), timeout_s=_CLEANUP_TIMEOUT_S)
        if not self._session.is_open:
            return
        try:
            async for _ in self._session.exec(spec):
                pass
        except CommandTimeoutError:
            return
