"""Define Deadline: how attach waits for a process it started to become usable, without a sleep.

A display server, a window manager, a sound server and a browser each take a moment to become
usable after they start, and how long varies with the machine. ADR-0031 makes readiness
functional: attach polls a real check (the display number Xvfb reports, the pointer moving and
reading back, the sound server answering, the browser's debugging port appearing) until it passes
or a deadline expires, never a fixed sleep that is too short on a slow Cell and wasted on a fast
one. `Deadline` is that bound, on the injected clock; `start_process` starts one process and turns
a program the Cell does not have into an AttachError; and `stop_all` is the one way everything
attach started is stopped again when a later step fails.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.attach`.
    Used by `attach.display`, `attach.audio` and `attach.core`. Calls into `hivemind.cell`
    (CellSession, BackgroundSpec, BackgroundProcess, BackgroundStartError),
    `hivemind.exoskeleton.errors` and `waggle.clock` only.

Key invariants:
    - Every wait is bounded by one Deadline; every poll pauses POLL_INTERVAL_S on the clock.
    - `stop_all` stops newest first and never raises for a process that is already gone.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for functional readiness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hivemind.cell import BackgroundProcess, BackgroundSpec, BackgroundStartError, CellSession
from hivemind.exoskeleton.errors import AttachError
from waggle.clock import Clock

POLL_INTERVAL_S = 0.05  # Short against a start-up of a second or more; cheap on the Cell.

__all__ = ["POLL_INTERVAL_S", "Deadline", "start_process", "stop_all"]


@dataclass(frozen=True, slots=True)
class Deadline:
    """A point on the clock's monotonic timeline that a readiness wait may not pass."""

    clock: Clock
    seconds: float  # The whole budget, for the error message when it runs out.
    expires_at: float  # `clock.monotonic()` value the wait ends at.

    @classmethod
    def after(cls, clock: Clock, seconds: float) -> Deadline:
        """Start a deadline `seconds` from now.

        Args:
            clock: The injected clock; a FakeClock makes waits instant in tests.
            seconds: The budget.

        Returns:
            The Deadline.
        """
        return cls(clock=clock, seconds=seconds, expires_at=clock.monotonic() + seconds)

    def expired(self) -> bool:
        """Return whether the budget is spent."""
        return self.clock.monotonic() >= self.expires_at

    async def pause(self) -> None:
        """Wait one poll interval before the next readiness check."""
        await self.clock.sleep(POLL_INTERVAL_S)


async def stop_all(session: CellSession, processes: Sequence[BackgroundProcess]) -> int:
    """Stop every process in `processes`, newest first; return how many were still running.

    Args:
        session: The session that started them.
        processes: Oldest first, as they were started.

    Returns:
        How many `session.stop` found running and stopped.
    """
    stopped = 0
    # Newest first: a window manager or browser goes before the display it is a client of, so
    # none of them logs a lost-display error on the way down. `stop` is idempotent and returns
    # False for anything already gone, a closed session's backstop included.
    for process in reversed(processes):
        stopped += int(await session.stop(process))
    return stopped


async def start_process(session: CellSession, spec: BackgroundSpec) -> BackgroundProcess:
    """Start one background process for attach, or explain why it could not start.

    Args:
        session: The Cell's session.
        spec: What to start.

    Returns:
        The started process.

    Raises:
        AttachError: The program is missing or cannot be executed on this Cell.
    """
    try:
        return await session.start(spec)
    except BackgroundStartError as error:
        raise AttachError(f"{spec.argv[0]} could not start on this Cell: {error.reason}") from error
