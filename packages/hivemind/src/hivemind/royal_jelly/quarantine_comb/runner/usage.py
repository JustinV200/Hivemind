"""Measure and bound a runner process: resource limits, usage figures, the wall deadline, output.

The runner is the one place that touches `resource` and `signal`. Before the tool runs it measures
what start-up already cost (a `Baseline`), applies the operating-system limits the pure plan in
`quarantine_comb.limits` derives from it, and arms a wall-clock deadline that interrupts whatever
the main thread is doing (a blocking sleep included, which `asyncio.wait_for` cannot). After the
phases it measures what the tool's work cost, net of that baseline, and reports how many bytes the
tool wrote to stdout and stderr. Every platform call is guarded: a limit or signal the platform
lacks is skipped silently, since the gates still judge the measured figures. The runner only ever
runs where a sandbox is (an Ubuntu Virtual Cell, or the development subprocess on a POSIX host),
but the module stays importable on Windows, because the Comb's own modules import it and a Windows
Hive Stand still composes a Comb.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Called by `runner.main`. Calls into `quarantine_comb.limits` (the pure plan), the standard
    library's `resource` and `signal`, and nothing in the Hive above it.

Key invariants:
    - `apply_limits` never raises: a limit that cannot be set is skipped, never fatal.
    - The deadline raises `DeadlineExceededError` (a `BaseException`, so the tool's own broad
      `except Exception` cannot swallow it) in the main thread and disarms itself when it fires.
    - Figures are net of the baseline taken before the tool's first line.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the limits and the deadline.
    - hivemind.royal_jelly.quarantine_comb.limits for the pure derivation of the caps.
"""

from __future__ import annotations

import os
import signal
import sys
import time
import types
from dataclasses import dataclass

from hivemind.royal_jelly.quarantine_comb.limits import Baseline, RlimitPlan
from hivemind.royal_jelly.quarantine_comb.runner.report import UsageSection

if sys.platform != "win32":
    import resource  # POSIX only; typeshed (and mypy's platform narrowing) agree.

_PAGE_FIELD = 0  # First field of /proc/self/statm: total program size, in pages.
_TAIL_KEEP_CHARS = 4_000  # How much of the tool's last output the writer keeps for a human.

__all__ = [
    "CountingWriter",
    "DeadlineExceededError",
    "Meter",
    "apply_limits",
    "arm_deadline",
    "disarm_deadline",
    "take_baseline",
]


class DeadlineExceededError(BaseException):
    """Raise in the main thread when the wall deadline fires; not an `Exception` on purpose."""


@dataclass(frozen=True, slots=True)
class Meter:
    """A reading of CPU and peak resident memory, taken at one moment."""

    cpu_s: float
    peak_rss_bytes: int
    wall_s: float

    @classmethod
    def now(cls) -> Meter:
        """Read this process's CPU seconds, peak resident bytes and a monotonic wall time."""
        # Explicit branches, so mypy checks each platform's half on its own platform only.
        if sys.platform == "win32":
            # No `resource` here: CPU time is still honest, the peak is unknown and reads as 0.
            return cls(time.process_time(), 0, time.monotonic())
        else:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # ru_maxrss is kilobytes on Linux and bytes on macOS; the scale differs, the meaning
            # not.
            scale = 1 if sys.platform == "darwin" else 1024
            return cls(usage.ru_utime + usage.ru_stime, usage.ru_maxrss * scale, time.monotonic())

    def since(self, start: Meter, output_bytes: int, timed_out_phase: str) -> UsageSection:
        """Return what happened between `start` and this reading, as the report section.

        Args:
            start: The baseline reading.
            output_bytes: Bytes the tool wrote to stdout and stderr.
            timed_out_phase: The phase the deadline interrupted; empty when none.
        """
        return UsageSection(
            cpu_s=max(0.0, self.cpu_s - start.cpu_s),
            peak_rss_bytes=max(0, self.peak_rss_bytes - start.peak_rss_bytes),
            wall_s=max(0.0, self.wall_s - start.wall_s),
            output_bytes=output_bytes,
            timed_out_phase=timed_out_phase,
        )


def take_baseline() -> Baseline:
    """Measure what this process has already used, for the limit plan to build on."""
    return Baseline(
        cpu_s=Meter.now().cpu_s,
        address_space_bytes=_virtual_size(),
        task_count=_user_task_count(),
    )


def apply_limits(plan: RlimitPlan) -> None:
    """Apply each limit in `plan` that this platform defines and allows; skip the rest silently.

    Args:
        plan: Limit constant names to `(soft, hard)` values.
    """
    if sys.platform == "win32":
        return  # No `resource` module: nothing to apply, and the gates still measure.
    else:
        for name, (soft, hard) in plan.limits.items():
            constant = getattr(resource, name, None)
            # A non-positive value means the platform could not tell: better no cap than a wrong
            # one.
            if constant is None or soft <= 0:
                continue
            try:
                _, current_hard = resource.getrlimit(constant)
                if current_hard != resource.RLIM_INFINITY:
                    hard = min(hard, current_hard)
                resource.setrlimit(constant, (min(soft, hard), hard))
            except (ValueError, OSError):
                continue  # The platform refused this one; the measured gates still apply.


def arm_deadline(seconds: float) -> bool:
    """Interrupt the main thread with `DeadlineExceededError` after `seconds` of wall time.

    Args:
        seconds: Wall seconds from now.

    Returns:
        False when the platform has no interval timer (the Comb's own kill is the only bound).
    """
    if sys.platform == "win32" or not hasattr(signal, "setitimer"):
        return False
    else:

        def _fire(_signum: int, _frame: types.FrameType | None) -> None:
            raise DeadlineExceededError

        signal.signal(signal.SIGALRM, _fire)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        return True


def disarm_deadline() -> None:
    """Cancel a pending deadline, so a late signal cannot interrupt the report being written."""
    if sys.platform != "win32" and hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, 0)


class CountingWriter:
    """A text sink that counts the bytes written to it and keeps the tail for a reader.

    Mutable state owned here alone (codingrules 8.5): the running byte count and the short tail.
    Stands in for stdout and stderr while the tool runs, so its printing is measured, not lost.
    """

    encoding = "utf-8"

    def __init__(self) -> None:
        """Start empty."""
        self.bytes_written = 0
        self.tail = ""

    def write(self, text: str) -> int:
        """Count `text` and keep the last `_TAIL_KEEP_CHARS` characters seen."""
        self.bytes_written += len(text.encode("utf-8", errors="replace"))
        self.tail = (self.tail + text)[-_TAIL_KEEP_CHARS:]
        return len(text)

    def flush(self) -> None:
        """Do nothing: there is no buffer to flush."""

    def isatty(self) -> bool:
        """Report that this is not a terminal."""
        return False


def _virtual_size() -> int:
    """Return this process's virtual size in bytes, or 0 where `/proc` does not say."""
    if sys.platform == "win32":
        return 0  # No /proc and no sysconf: the address-space cap is skipped.
    else:
        try:
            with open("/proc/self/statm", encoding="ascii") as statm:  # No Path needed.
                pages = int(statm.read().split()[_PAGE_FIELD])
        except (OSError, ValueError, IndexError):
            return 0
        return pages * os.sysconf("SC_PAGE_SIZE")


def _user_task_count() -> int:
    """Return how many tasks this user already runs, or 0 where `/proc` does not say.

    RLIMIT_NPROC counts every task of the real user across the machine, threads included, so
    the baseline must count the same thing: `/proc/<pid>/status`'s `Threads` for each process
    whose real uid is this one. Counting processes alone undercounts a user who runs threaded
    programs (a CI runner's own agent holds hundreds), and a cap below what the user already
    runs makes the next thread or fork fail at once.
    """
    if sys.platform == "win32":
        return 0  # No /proc and no uid: the process cap is skipped.
    else:
        try:
            entries = [entry for entry in os.listdir("/proc") if entry.isdigit()]
        except OSError:
            return 0
        return sum(_threads_of(entry, os.getuid()) for entry in entries)


def _threads_of(pid: str, uid: int) -> int:
    """Return how many threads process `pid` runs if its real uid is `uid`, else 0."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as status:
            fields = dict(line.split(":", 1) for line in status if ":" in line)
    except OSError:
        return 0  # The process ended between the listing and the read.
    # `Uid:` lists real, effective, saved and filesystem uids; the real one is the limit's.
    if int(fields.get("Uid", "-1").split()[0]) != uid:
        return 0
    return int(fields.get("Threads", "1").split()[0])
