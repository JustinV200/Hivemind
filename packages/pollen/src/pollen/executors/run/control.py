"""Hold the gateway's hand on a running exec: why it ended, its signals and its watchdog.

When the Pollen Packet (the thin gateway program on a device the Hive borrows) ends an exec itself
rather than letting the program finish, it must say why: a timeout (TIMED_OUT), a crossed scratch
quota, an output cap, a signal that arrived on stdin, or the release of the lease (the Hive's
temporary hold on the device), each reported as KILLED with a reason. ``RunControl`` keeps that
one verdict (the first reason wins, so a later cause cannot rewrite what really happened), sends
the signals to the program's whole process group, and runs the watchdog that samples elapsed time
and the scratch directory's size until a limit trips. A signal that arrives before the program has
started is remembered and delivered the moment it has.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.executors.run.runner; signals through the platform shim and measures through
    pollen.lease.quota; calls into waggle and the standard library only.

Key invariants:
    - The verdict is set at most once.
    - Every signal and kill goes to the whole process group.
    - The watchdog marks itself as killing before it starts a kill, so the run waits for the kill
      to finish instead of cancelling it half way.

See Also:
    - docs/waggle/gateway-contract.md section 11 (the end of an exec).
    - pollen.executors.run.runner for the run this controls.
"""

from __future__ import annotations

import asyncio
import signal

from pollen.executors.base import ExecutorDeps
from pollen.executors.run.plan import ExecPlan
from pollen.lease.quota import QUOTA_SAMPLE_INTERVAL_S, directory_size_bytes
from pollen.platform.base import ProcessRef, stop_group
from waggle.messages.session import ProcessSignal, SessionOutcome

MAX_SIGNAL_NAME_CHARS = 16  # The wire bound of SessionExit.signal_name.

type RunResult = tuple[
    SessionOutcome, int | None, str | None, str
]  # Outcome, code, signal, reason.

__all__ = ["MAX_SIGNAL_NAME_CHARS", "RunControl", "RunResult"]


class RunControl:
    """The verdict, the signals and the watchdog of one exec.

    Owns mutable state (codingrules 8.5, documented): the process reference, the verdict and the
    early signal, touched by the run's own tasks and by the reader through ``deliver``.
    """

    def __init__(self, plan: ExecPlan, deps: ExecutorDeps) -> None:
        """Bind the control to one exec.

        Args:
            plan: The exec whose limits the watchdog enforces.
            deps: The clock and platform shim the kills and the watchdog use.
        """
        self._plan = plan
        self._deps = deps
        self._ref: ProcessRef | None = None
        self._verdict: tuple[SessionOutcome, str] | None = None
        self._early_signal: ProcessSignal | None = None
        self.is_killing = False  # The watchdog is mid-kill and the run must let it finish.

    @property
    def has_verdict(self) -> bool:
        """Whether the gateway has already decided to end the run."""
        return self._verdict is not None

    def attach(self, ref: ProcessRef) -> None:
        """Learn the process just started, and deliver a signal that arrived before it did.

        Args:
            ref: The program's pid and creation time.
        """
        self._ref = ref
        if self._early_signal is not None:
            self.deliver(self._early_signal)

    def set_verdict(self, outcome: SessionOutcome, reason: str) -> None:
        """Record why the gateway ended the run; the first reason wins."""
        if self._verdict is None:
            self._verdict = (outcome, reason)

    def deliver(self, which: ProcessSignal) -> None:
        """Send a stdin signal to the group, or hold it until the program has started."""
        if self._ref is None:
            self._early_signal = which
            return
        self.set_verdict(SessionOutcome.KILLED, "A stdin signal ended it.")
        self._deps.shim.signal_group(self._ref, which)

    def kill_now(self) -> None:
        """Force-kill the whole group at once (the output cap, a cancelled run)."""
        if self._ref is not None:
            self._deps.shim.signal_group(self._ref, ProcessSignal.KILL)

    def on_output_cap(self) -> None:
        """Handle the output cap being passed: kill the group at once and say why."""
        self.set_verdict(SessionOutcome.KILLED, "Output passed its cap; the group was killed.")
        self.kill_now()

    async def end_group(self, *, graceful: bool) -> bool:
        """Stop the whole group, asking first when ``graceful``; True if it was still alive."""
        if self._ref is None:
            return False
        return await stop_group(
            self._deps.shim, self._ref, graceful=graceful, clock=self._deps.clock
        )

    def result(self, returncode: int) -> RunResult:
        """Turn the verdict, or else the return code, into the exit's outcome and fields.

        Args:
            returncode: What the program returned (negative for a POSIX signal).

        Returns:
            The outcome, the exit code (EXITED only), the signal name (SIGNALED only) and a
            reason.
        """
        if self._verdict is not None:
            outcome, reason = self._verdict
            return outcome, None, None, reason
        if returncode >= 0:
            return SessionOutcome.EXITED, returncode, None, "The program exited."
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"SIG{-returncode}"
        return SessionOutcome.SIGNALED, None, name[:MAX_SIGNAL_NAME_CHARS], "A signal ended it."

    async def watch(self, started: float) -> None:
        """Enforce the timeout and the scratch quota until the run is over or one trips.

        Args:
            started: The clock's monotonic reading when the program started.
        """
        plan, clock = self._plan, self._deps.clock
        while True:
            remaining = plan.timeout_s - (clock.monotonic() - started)
            await clock.sleep(min(QUOTA_SAMPLE_INTERVAL_S, max(remaining, 0.001)))
            if clock.monotonic() - started >= plan.timeout_s:
                reason = f"It ran past its {plan.timeout_s:g} s timeout."
                await self._trip(SessionOutcome.TIMED_OUT, reason, graceful=True)
                return
            used = await asyncio.to_thread(directory_size_bytes, plan.scratch_root)
            if used > plan.quota_bytes:
                reason = f"Scratch passed its quota of {plan.quota_bytes} bytes."
                await self._trip(SessionOutcome.KILLED, reason, graceful=False)
                return

    async def _trip(self, outcome: SessionOutcome, reason: str, *, graceful: bool) -> None:
        """A limit was crossed: set the verdict and end the group, letting nothing cancel it."""
        self.set_verdict(outcome, reason)
        self.is_killing = True
        await self.end_group(graceful=graceful)
