"""Parse what the probe's script printed about processes and resources into wire value models.

A Warden watching a Cell it is local to runs one fixed script through its session
(`hivemind.wardens.watch.probe.commands`); this module turns the text that came back into the same
`ProcessSample` and `ResourceSample` values a Pollen Packet's collectors send, with the same caps,
so the observer, the signals and the Patrol never know which probe produced a snapshot. Every
function is pure and total: a section that is missing or malformed gives an empty result, never an
error, because the text is whatever the machine printed (and a process may exit between two reads
of it). The script's output is split into sections by marker lines that carry a random mark the
probe chose for that run, so nothing the machine holds (a process name with a newline in it, a
file name that looks like a marker) can open, close or replace a section.

A process's CPU share is the part of the whole machine's CPU it used since the previous sample
(0 to 1; one core of four busy is 0.25). `CpuBaseline` keeps the previous sample's cumulative CPU
ticks per process, so the share is a real delta; a process seen for the first time has no baseline
and gets the average over its own life, which for a process that has only just started is what it
has used since the previous sample too.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch.probe sub-package. Used by `hivemind.wardens.watch.probe.session.SessionWatchProbe`
    only. Calls into `hivemind.wardens.watch.probe.commands` and waggle's swarm messages only.

Key invariants:
    - Nothing here raises for malformed input; it returns less.
    - Every list is cut at the bound the wire model allows, busiest first, and the cut is reported.
    - `CpuBaseline` forgets a process the moment a sample no longer lists it, so it never holds
      more than one sample's worth of processes.

See Also:
    - pollen.executors.observe.system for the packet's own collectors, whose figures these match.
    - waggle.messages.swarm.watch for the value models and their bounds.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from hivemind.wardens.watch.probe.commands import USER_HZ
from waggle.messages.swarm import NetworkCounter, ProcessSample, ResourceSample
from waggle.messages.swarm.watch import (
    MAX_INTERFACE_CHARS,
    MAX_NETWORK_INTERFACES,
    MAX_PROCESS_NAME_CHARS,
)

DEFAULT_PAGE_SIZE = 4_096  # Bytes per memory page when the platform will not say (nearly all x86).
_KIB = 1_024  # /proc/meminfo and df -k count in KiB.
_STAT_FIELDS_NEEDED = 22  # Fields after the name that a /proc/<pid>/stat line must have for rss.
_UTIME, _STIME, _STARTTIME, _RSS = 11, 12, 19, 21  # Their index among the fields after the state.

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "CpuBaseline",
    "KernelFacts",
    "RawProcess",
    "parse_kernel",
    "parse_processes",
    "parse_resources",
    "process_samples",
    "split_sections",
]


@dataclass(frozen=True, slots=True)
class KernelFacts:
    """The machine's own constants and clock, read from the same script run as the processes."""

    cores: int  # Logical CPUs, at least 1.
    boot_epoch: float  # When the machine booted, in seconds since the Unix epoch.
    uptime_s: float  # Seconds since boot at the moment of the read.
    page_size: int  # Bytes in one memory page, for converting a process's resident pages.


@dataclass(frozen=True, slots=True)
class RawProcess:
    """One /proc/<pid>/stat line, reduced to what a sample needs."""

    pid: int
    name: str  # The kernel's name for it: at most 15 characters, never its arguments.
    cpu_ticks: int  # User plus system CPU time used since it started, in USER_HZ ticks.
    start_ticks: int  # When it started, in USER_HZ ticks since boot.
    rss_bytes: int  # Resident memory.


def split_sections(output: str, mark: str) -> dict[str, str]:
    """Split the script's output into its sections.

    Args:
        output: Everything the script printed on stdout.
        mark: The run's random token; a marker line is `<mark> <name>`. Nothing else on the
            machine can know it, so no file name or log line can open or close a section.

    Returns:
        Section name to its text, lines joined by newlines. Text before the first marker is
        dropped.
    """
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    prefix = f"{mark} "
    # Split on the newline alone: a name may hold any other line separator, and must keep it.
    for line in output.split("\n"):
        if line.startswith(prefix):
            current = sections.setdefault(line[len(prefix) :].strip(), [])
        elif current is not None:
            current.append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def parse_kernel(sections: Mapping[str, str]) -> KernelFacts | None:
    """Read the machine's core count, boot time, uptime and page size.

    Args:
        sections: The script's sections, from `split_sections`.

    Returns:
        The facts, or None when the clock or the core count is missing (nothing about a process
        can be put in proportion without them).
    """
    stat_lines = sections.get("stat", "").splitlines()
    cores = sum(1 for line in stat_lines if _is_cpu_line(line))
    boot = next(
        (_number(line.split()[1:]) for line in stat_lines if line.startswith("btime ")), None
    )
    uptime = _number(sections.get("uptime", "").split())
    if cores < 1 or boot is None or uptime is None:
        return None
    page = _number(sections.get("pagesize", "").split())
    page_size = int(page) if page is not None and page > 0 else DEFAULT_PAGE_SIZE
    return KernelFacts(cores=cores, boot_epoch=boot, uptime_s=uptime, page_size=page_size)


def parse_processes(text: str, facts: KernelFacts) -> list[RawProcess]:
    """Read every process out of the concatenated `/proc/<pid>/stat` lines.

    Args:
        text: The `procs` section.
        facts: The machine's constants, for the page size.

    Returns:
        One entry per well-formed line, in the order printed; a line a process's exit cut short,
        or one a hostile name split in two, is skipped.
    """
    found: list[RawProcess] = []
    for line in text.splitlines():
        process = _parse_stat_line(line, facts.page_size)
        if process is not None:
            found.append(process)
    return found


def process_samples(
    processes: Sequence[RawProcess], facts: KernelFacts, baseline: CpuBaseline, max_items: int
) -> tuple[tuple[ProcessSample, ...], bool]:
    """Turn raw processes into wire samples, busiest first, cut at `max_items`.

    Args:
        processes: What `parse_processes` read.
        facts: The machine's constants and clock.
        baseline: The previous sample's CPU ticks, replaced by this sample's as a side effect.
        max_items: The most processes to return (at most the wire bound).

    Returns:
        The samples and whether any process was left out.
    """
    shares = baseline.shares(processes, facts)
    samples = [
        ProcessSample(
            pid=process.pid,
            name=process.name[:MAX_PROCESS_NAME_CHARS],
            cpu_share=share,
            rss_bytes=process.rss_bytes,
            started_at=datetime.fromtimestamp(
                facts.boot_epoch + process.start_ticks / USER_HZ, UTC
            ),
        )
        for process, share in zip(processes, shares, strict=True)
    ]
    # Busiest first, so a cut loses the least busy; memory breaks ties between idle processes.
    samples.sort(key=lambda sample: (sample.cpu_share, sample.rss_bytes), reverse=True)
    return tuple(samples[:max_items]), len(samples) > max_items


def parse_resources(sections: Mapping[str, str], facts: KernelFacts) -> ResourceSample | None:
    """Read load, memory, disk, uptime and the network counters into one sample.

    Args:
        sections: The script's sections.
        facts: The machine's constants and clock.

    Returns:
        The sample, or None when neither memory nor disk figures were printed (nothing to report).
    """
    memory = _meminfo(sections.get("meminfo", ""))
    disk = _df(sections.get("df", ""))
    if memory is None and disk is None:
        return None
    total_memory, free_memory = memory if memory is not None else (0, 0)
    total_disk, free_disk = disk if disk is not None else (0, 0)
    load = _number(sections.get("loadavg", "").split())
    return ResourceSample(
        cpu_load=0.0 if load is None else load / facts.cores,
        memory_bytes=total_memory,
        memory_free_bytes=min(free_memory, total_memory),
        disk_bytes=total_disk,
        disk_free_bytes=min(free_disk, total_disk),
        uptime_s=facts.uptime_s,
        network=_netdev(sections.get("netdev", "")),
    )


class CpuBaseline:
    """Remember each process's CPU ticks at the previous sample, to measure the share since then.

    Owns mutable state (codingrules 8.5, documented): the previous sample's ticks by process, and
    when it was taken. A process is identified by its pid and start time together, so a pid the
    machine reuses is a new process with no baseline.
    """

    def __init__(self) -> None:
        """Start with no previous sample, so the first one measures lifetime averages."""
        self._ticks: dict[tuple[int, int], int] = {}
        self._uptime_s: float | None = None

    def shares(self, processes: Sequence[RawProcess], facts: KernelFacts) -> list[float]:
        """Return each process's share of the whole machine's CPU, and keep this sample as the base.

        Args:
            processes: The processes of this sample.
            facts: The machine's cores and uptime at this sample.

        Returns:
            One share per process, in the same order, each between 0 and 1.
        """
        span = None if self._uptime_s is None else facts.uptime_s - self._uptime_s
        shares = [self._share(process, facts, span) for process in processes]
        # Replace, never merge: a process that has gone must not stay in the baseline forever.
        self._ticks = {
            (process.pid, process.start_ticks): process.cpu_ticks for process in processes
        }
        self._uptime_s = facts.uptime_s
        return shares

    def _share(self, process: RawProcess, facts: KernelFacts, span: float | None) -> float:
        """Return one process's share since the previous sample, or since it started."""
        before = self._ticks.get((process.pid, process.start_ticks))
        if before is not None and span is not None and span > 0:
            used_s = max(0, process.cpu_ticks - before) / USER_HZ
            return _clamp(used_s / span / facts.cores)
        lifetime_s = facts.uptime_s - process.start_ticks / USER_HZ
        if lifetime_s <= 0:
            return 0.0  # Started in the instant of the read: nothing to divide by.
        return _clamp(process.cpu_ticks / USER_HZ / lifetime_s / facts.cores)


# ──────────────────────────────────────────────────────────────────────────────
# Line parsers
# ──────────────────────────────────────────────────────────────────────────────


def _is_cpu_line(line: str) -> bool:
    """Return whether `line` is a per-CPU row of /proc/stat (`cpu0 ...`), not the `cpu ` total."""
    return line.startswith("cpu") and line[3:4].isdigit()


def _number(tokens: Sequence[str]) -> float | None:
    """Return the first token as a number, or None when there is none or it is not one."""
    try:
        return float(tokens[0])
    except (IndexError, ValueError):
        return None


def _clamp(share: float) -> float:
    """Keep a share between 0 and 1: a sampling quirk must never break the wire bound."""
    return min(1.0, max(0.0, share))


def _parse_stat_line(line: str, page_size: int) -> RawProcess | None:
    """Parse one `/proc/<pid>/stat` line, or None when it is not one."""
    open_paren, close_paren = line.find(" ("), line.rfind(")")
    # The name sits between the first " (" and the LAST ")": it may itself hold spaces and parens.
    if open_paren < 1 or close_paren < open_paren or not line[:open_paren].isdigit():
        return None
    fields = line[close_paren + 1 :].split()
    if len(fields) < _STAT_FIELDS_NEEDED:
        return None
    try:
        return RawProcess(
            pid=int(line[:open_paren]),
            name=line[open_paren + 2 : close_paren],
            cpu_ticks=int(fields[_UTIME]) + int(fields[_STIME]),
            start_ticks=int(fields[_STARTTIME]),
            rss_bytes=max(0, int(fields[_RSS])) * page_size,
        )
    except ValueError:
        return None


def _meminfo(text: str) -> tuple[int, int] | None:
    """Return (total, available) memory in bytes from /proc/meminfo, or None without a total."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        number = rest.split()[:1]
        if number and number[0].isdigit():
            values[key] = int(number[0]) * _KIB
    if "MemTotal" not in values:
        return None
    # MemAvailable is what can start work without swapping; very old kernels only have MemFree.
    return values["MemTotal"], values.get("MemAvailable", values.get("MemFree", 0))


def _df(text: str) -> tuple[int, int] | None:
    """Return (size, available) bytes from `df -Pk`'s one data row, or None when there is none."""
    rows = [line.split() for line in text.splitlines() if line.strip()]
    for columns in rows[1:]:  # The first row is the header.
        if len(columns) >= 4 and columns[1].isdigit() and columns[3].isdigit():
            return int(columns[1]) * _KIB, int(columns[3]) * _KIB
    return None


def _netdev(text: str) -> tuple[NetworkCounter, ...]:
    """Return one counter per interface in /proc/net/dev, sorted, unique and within the bound."""
    counters: dict[str, NetworkCounter] = {}
    for line in text.splitlines():
        name, separator, rest = line.partition(":")
        columns = rest.split()
        name = name.strip()[:MAX_INTERFACE_CHARS]
        if not separator or not name or len(columns) < 9 or name in counters:
            continue  # A header line, a blank, or an interface whose cut name repeats an earlier.
        if columns[0].isdigit() and columns[8].isdigit():
            counters[name] = NetworkCounter(
                interface=name, rx_bytes=int(columns[0]), tx_bytes=int(columns[8])
            )
    return tuple(counters[name] for name in sorted(counters))[:MAX_NETWORK_INTERFACES]
