"""Show the Patrol its window: trusted framing, and the machine's words as quoted data.

A Patrol (a watching Warden's hourly review, ADR-0054) is one awake episode over everything its
Cell showed since the last one. This module turns that window into the two texts the episode's
event carries. `summary_of` is the Hive's own framing and holds counts and times only: how many
observations, over what period, how many signals of each kind. `facts_of` is everything the machine
said, in the order the Patrol prompt promises: the signals in detail, the latest sample, the
busiest processes, the network traffic, the newest log lines and the files that changed. Every
piece of text the machine supplied (a process name, a path, a log line) is JSON-quoted and cut to a
short length, so it stays on its own line and cannot pass for the Hive's structure; the whole block
is bounded, later sections giving way first. The bridge scans it and fences it
(`hivemind.memory.render_untrusted`), so a log line can never speak to the model as the Hive.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Called by `hivemind.wardens.watch.patrol` (to build the window) and
    `.bridge` (to build the event). Calls into `hivemind.wardens.watch.signals` and waggle's swarm
    messages only.

Key invariants:
    - `summary_of` holds no text the machine supplied: it is safe to state as the Hive's own.
    - `facts_of` quotes every machine-supplied string and never exceeds `MAX_FACT_CHARS`.
    - Nothing here is a trail payload or a log line.

See Also:
    - llm/prompts/patrol.md for what the model is told to expect, in this order.
    - hivemind.memory.hot_state.untrusted for how the fenced block is rendered.
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the Patrol.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from hivemind.wardens.watch.signals import SignalKind, WatchSignal
from waggle.messages.swarm import Observation

MAX_FACT_CHARS = 12_000  # The whole block; a Patrol's prompt holds an hour, not a day, of detail.
MAX_ITEM_CHARS = 240  # One machine-supplied string, before quoting: a name, a path, a line.
MAX_BUSY = 10  # Processes listed, by their peak share of the CPU.
MAX_LOGS = 30  # Newest log lines listed.
MAX_CHANGES = 30  # Newest file changes listed.
MAX_NETWORK = 8  # Interfaces listed, busiest first.
_MIB = 1_048_576

__all__ = [
    "MAX_FACT_CHARS",
    "PatrolWindow",
    "counts_of",
    "facts_of",
    "summary_of",
]


@dataclass(frozen=True, slots=True)
class PatrolWindow:
    """Everything one Patrol reviews: the observations since the last one, and the signals."""

    since: datetime  # When the oldest observation in it was kept.
    until: datetime  # When the newest was; a decision carried out moves the Patrol's cursor here.
    observations: tuple[Observation, ...]  # Oldest first.
    signals: tuple[WatchSignal, ...]


def counts_of(window: PatrolWindow) -> str:
    """Describe a window by its counts and period alone: nothing the machine said.

    Args:
        window: What a Patrol reviews.

    Returns:
        The observations, the period and the signals by kind, as a clause; also what an Alarm
        or a Cell Wax proposal says of itself, since neither may carry a line from the machine.
    """
    kinds = Counter(signal.kind.value for signal in window.signals)
    by_kind = ", ".join(f"{name} {count}" for name, count in sorted(kinds.items()))
    signals = f"{len(window.signals)} signal{'s' if len(window.signals) != 1 else ''}"
    since, until = (at.isoformat(timespec="seconds") for at in (window.since, window.until))
    return (
        f"{len(window.observations)} observations from {since} to {until}; "
        f"{signals}{f' ({by_kind})' if by_kind else ''}"
    )


def summary_of(window: PatrolWindow) -> str:
    """Frame the Patrol in the Hive's own words: counts and times, nothing the machine said.

    Args:
        window: What this Patrol reviews.

    Returns:
        One sentence naming the period, the observations and the signals by kind.
    """
    return f"Patrol of your Cell over {counts_of(window)}."


def facts_of(window: PatrolWindow) -> str:
    """Render what the machine said over the window, as quoted data lines.

    Args:
        window: What this Patrol reviews.

    Returns:
        The facts block, at most `MAX_FACT_CHARS` long: whole lines only, earlier sections first.
    """
    lines: list[str] = []
    size = 0
    for line in _lines(window):
        size += len(line) + 1
        if size > MAX_FACT_CHARS:
            lines.append("... (further facts omitted to fit)")
            break
        lines.append(line)
    return "\n".join(lines)


def _lines(window: PatrolWindow) -> list[str]:
    """Return every fact line, in the order the Patrol prompt names its sections."""
    return [
        *(_signal_line(signal) for signal in window.signals),
        *_latest_sample(window.observations),
        *_busiest(window.observations),
        *_network(window.observations),
        *_logs(window.observations),
        *_changes(window.observations),
    ]


def _q(text: str) -> str:
    """Quote machine-supplied text: cut, then JSON-escaped, so it is one line and plainly data."""
    return json.dumps(text[:MAX_ITEM_CHARS])


def _signal_line(signal: WatchSignal) -> str:
    """Render one signal with the fields its rule measured."""
    at = signal.at.isoformat(timespec="seconds")
    if signal.kind is SignalKind.NEW_PROCESS_CPU:
        return (
            f"signal {signal.kind.value}: name={_q(signal.subject)} pid={signal.pid} "
            f"cpu_share={signal.value:.2f} at={at}"
        )
    if signal.kind is SignalKind.DISK_FILLING:
        return f"signal {signal.kind.value}: disk_used={signal.value:.2f} at={at}"
    return (
        f"signal {signal.kind.value}: name={_q(signal.subject)} starts={signal.value:.0f} at={at}"
    )


def _latest_sample(observations: Sequence[Observation]) -> list[str]:
    """Render the newest resource sample: load, memory and disk use, uptime."""
    for observation in reversed(observations):
        resources = observation.resources
        if resources is None:
            continue
        memory = (
            1 - resources.memory_free_bytes / resources.memory_bytes
            if resources.memory_bytes
            else 0
        )
        disk = 1 - resources.disk_free_bytes / resources.disk_bytes if resources.disk_bytes else 0
        return [
            f"latest sample at {observation.observed_at.isoformat(timespec='seconds')}: "
            f"cpu_load={resources.cpu_load:.2f} memory_used={memory:.2f} disk_used={disk:.2f} "
            f"uptime_s={resources.uptime_s:.0f}"
        ]
    return []


def _busiest(observations: Sequence[Observation]) -> list[str]:
    """Render the processes with the highest peak CPU share anywhere in the window."""
    peaks: dict[tuple[int, str], tuple[float, int]] = {}
    for observation in observations:
        for process in observation.processes:
            key = (process.pid, process.name)
            share, rss = peaks.get(key, (0.0, 0))
            peaks[key] = (max(share, process.cpu_share), max(rss, process.rss_bytes))
    top = sorted(peaks.items(), key=lambda item: (-item[1][0], item[0][1], item[0][0]))[:MAX_BUSY]
    return [
        f"process name={_q(name)} pid={pid} peak_cpu_share={share:.2f} peak_rss_mb={rss // _MIB}"
        for (pid, name), (share, rss) in top
    ]


def _network(observations: Sequence[Observation]) -> list[str]:
    """Render how much each interface carried between the window's first and last sample."""
    sampled = [o.resources for o in observations if o.resources is not None]
    if len(sampled) < 2:
        return []
    first = {c.interface: c for c in sampled[0].network}
    moved: list[tuple[int, str, int, int]] = []
    for counter in sampled[-1].network:
        before = first.get(counter.interface)
        if before is not None:
            # A counter that went backwards means the machine restarted; show what it has now.
            rx = (
                counter.rx_bytes - before.rx_bytes
                if counter.rx_bytes >= before.rx_bytes
                else counter.rx_bytes
            )
            tx = (
                counter.tx_bytes - before.tx_bytes
                if counter.tx_bytes >= before.tx_bytes
                else counter.tx_bytes
            )
            moved.append((rx + tx, counter.interface, rx, tx))
    moved.sort(key=lambda row: (-row[0], row[1]))
    return [
        f"network interface={_q(name)} rx_bytes={rx} tx_bytes={tx}"
        for _, name, rx, tx in moved[:MAX_NETWORK]
    ]


def _logs(observations: Sequence[Observation]) -> list[str]:
    """Render the newest distinct log lines; each sample re-reads a log's tail, so they repeat."""
    distinct = dict.fromkeys((line.path, line.line) for o in observations for line in o.log_lines)
    newest = list(distinct)[-MAX_LOGS:]
    return [f"log path={_q(path)} line={_q(line)}" for path, line in newest]


def _changes(observations: Sequence[Observation]) -> list[str]:
    """Render the newest distinct file changes."""
    distinct = dict.fromkeys(
        (change.change.value, change.path, change.at, change.size_bytes)
        for o in observations
        for change in o.file_changes
    )
    newest = list(distinct)[-MAX_CHANGES:]
    return [
        f"change {kind} path={_q(path)} at={at.isoformat(timespec='seconds')} size={size}"
        for kind, path, at, size in newest
    ]
