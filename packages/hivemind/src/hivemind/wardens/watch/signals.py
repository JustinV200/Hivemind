"""Pick out, with fixed rules and no model, what in a window a Patrol should not miss.

A Patrol (a watching Warden's hourly review, ADR-0054) reads everything its Cell showed it since
the last one, and a model reading raw process lists would drown. This module is the cheap
pre-filter that runs first: three rules over the window's Observations that turn the facts a Patrol
should not miss into typed `WatchSignal`s, handed to the model beside the raw data as labelled
facts, never as conclusions. A new process using more of the CPU than `cpu_share_alarm` (it started
inside the window, so it is not the machine's ordinary load); the disk filled past
`disk_fill_alarm` (read from the newest sample, since only the present matters); and a restart loop
(one name seen as three or more different processes in turn, never two at once, which is a daemon
that keeps dying and being started again; a browser's many tabs or a server's workers run together,
so they are not). The functions are pure and total: they raise for no input, return at most
`MAX_PER_KIND` signals of each kind (the worst first) and give the same answer for the same window.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Called by `hivemind.wardens.watch.patrol`; read by `.digest`. Calls into
    waggle's swarm messages only.

Key invariants:
    - No signal is a decision: it is a labelled fact. What to do about it is the Patrol's.
    - A signal carries a process name, which is whatever the machine calls it: it is untrusted
      text and the digest quotes it as data.
    - Raising a threshold never adds a signal (a hypothesis test pins it).

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the three rules.
    - hivemind.wardens.watch.digest for how the signals reach the model.
    - hivemind.manifest.schema.supervision.WatchSection for the two thresholds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from waggle.messages.swarm import Observation, ProcessSample

MAX_PER_KIND = 10  # Signals kept of each kind; the worst first. More is noise for a model.
RESTART_LOOP_MIN_STARTS = 3  # One name started this many times, one after another, is a loop.
NEW_PROCESS_SLACK = timedelta(seconds=10)  # Boot times drift a few seconds; so does "started".

type Identity = tuple[int, datetime | None]  # A process is its pid and when it started.

__all__ = [
    "MAX_PER_KIND",
    "NEW_PROCESS_SLACK",
    "RESTART_LOOP_MIN_STARTS",
    "SignalKind",
    "WatchSignal",
    "find_signals",
]


class SignalKind(Enum):
    """The three rules the pre-filter applies."""

    NEW_PROCESS_CPU = "NEW_PROCESS_CPU"  # A process that started in the window, using a lot of CPU.
    DISK_FILLING = "DISK_FILLING"  # The disk is nearly full now.
    RESTART_LOOP = "RESTART_LOOP"  # One name keeps dying and being started again.


@dataclass(frozen=True, slots=True)
class WatchSignal:
    """One labelled fact the pre-filter found in a window.

    Attributes:
        kind: Which rule found it.
        subject: A process name (untrusted: the machine's own word) or `disk`.
        value: The number that crossed: the CPU share, the disk fill or the count of starts.
        at: When the sample that shows it was taken.
        pid: The process id the fact is about, where it is about one process.
    """

    kind: SignalKind
    subject: str
    value: float
    at: datetime
    pid: int | None = None


def find_signals(
    observations: Sequence[Observation], cpu_share_alarm: float, disk_fill_alarm: float
) -> tuple[WatchSignal, ...]:
    """Apply the three rules to a window of Observations.

    Args:
        observations: The window, oldest first. An empty window has no signals.
        cpu_share_alarm: A new process at or above this share of the CPU (0 to 1) is a signal.
        disk_fill_alarm: A disk filled to at least this fraction (0 to 1) is a signal.

    Returns:
        The signals, new processes first, then the disk, then restart loops; within a kind the
        worst first, at most `MAX_PER_KIND` of each.
    """
    if not observations:
        return ()
    return (
        *_top(_new_processes(observations, cpu_share_alarm)),
        *_disk(observations, disk_fill_alarm),
        *_top(_restart_loops(observations)),
    )


def _top(signals: list[WatchSignal]) -> list[WatchSignal]:
    """Keep the `MAX_PER_KIND` biggest of one kind, biggest first, ties by name."""
    return sorted(signals, key=lambda signal: (-signal.value, signal.subject))[:MAX_PER_KIND]


def _new_processes(observations: Sequence[Observation], limit: float) -> list[WatchSignal]:
    """Return one signal per process that started in the window and ever used `limit` of the CPU."""
    window_start = observations[0].observed_at - NEW_PROCESS_SLACK
    seen: set[Identity] = set()
    peaks: dict[Identity, WatchSignal] = {}
    for index, observation in enumerate(observations):
        for process in observation.processes:
            identity = (process.pid, process.started_at)
            best = peaks.get(identity)
            is_louder = best is None or process.cpu_share > best.value
            if (
                is_louder
                and process.cpu_share >= limit
                and _is_new(process, index, seen, window_start)
            ):
                peaks[identity] = WatchSignal(
                    SignalKind.NEW_PROCESS_CPU,
                    process.name,
                    process.cpu_share,
                    observation.observed_at,
                    process.pid,
                )
            seen.add(identity)
    return list(peaks.values())


def _is_new(
    process: ProcessSample, index: int, seen: set[Identity], window_start: datetime
) -> bool:
    """Return whether `process`, in the sample at `index`, started inside the window."""
    # A start time says it directly. Where the platform gives none, a process absent from every
    # earlier sample is new, and the first sample is the baseline, so nothing in it is.
    if process.started_at is not None:
        return process.started_at >= window_start
    return index > 0 and (process.pid, None) not in seen


def _disk(observations: Sequence[Observation], limit: float) -> list[WatchSignal]:
    """Return the disk signal if the newest sample that measured the disk shows it that full."""
    for observation in reversed(observations):
        resources = observation.resources
        if resources is not None and resources.disk_bytes > 0:
            fill = 1 - resources.disk_free_bytes / resources.disk_bytes
            if fill >= limit:
                return [WatchSignal(SignalKind.DISK_FILLING, "disk", fill, observation.observed_at)]
            return []  # The newest measurement is the present; an older, fuller one is history.
    return []


def _restart_loops(observations: Sequence[Observation]) -> list[WatchSignal]:
    """Return one signal per name seen as three or more processes in turn, never two at once."""
    starts: dict[str, set[Identity]] = {}
    together: set[str] = set()  # Names that had two processes in one sample: not a restart loop.
    last: dict[str, tuple[datetime, int]] = {}
    for observation in observations:
        in_sample: dict[str, Identity] = {}
        for process in observation.processes:
            identity = (process.pid, process.started_at)
            if in_sample.setdefault(process.name, identity) != identity:
                together.add(process.name)
            starts.setdefault(process.name, set()).add(identity)
            last[process.name] = (observation.observed_at, process.pid)
    return [
        WatchSignal(SignalKind.RESTART_LOOP, name, float(len(identities)), *last[name])
        for name, identities in starts.items()
        if len(identities) >= RESTART_LOOP_MIN_STARTS and name not in together
    ]
