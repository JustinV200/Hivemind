"""Collect the process list and the resource figures: the system half of watch mode.

A Warden (the per-Cell supervisor) watches an idle Real Cell (an existing device the Hive borrows
for a task) by asking its Pollen Packet (the thin gateway program on the device) what the device
looks like, read-only (ADR-0054). These are two of the four fixed collectors: the process list,
busiest first, with each process's name (never its arguments, which can carry secrets), its share
of the whole machine's CPU since the previous sample and its resident memory; and the resource
sample, a full snapshot of load, memory, disk, uptime and the network counters. They read the
platform's own facilities through psutil and run no command, so nothing a request says can shape
what is executed. A figure the platform will not give is zero, never a guess.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by
    pollen.executors.observe.executor; reads the host through psutil and pollen.executors.info;
    calls into waggle value models and the standard library only.

Key invariants:
    - Read-only: nothing here writes, signals or starts anything.
    - A process's CPU share is since the previous call (psutil keeps the sample per process); the
      first call is primed by the caller, and the share is clamped to between 0 and 1.
    - Every list is cut at the bound the wire model allows, busiest first, and says so.

See Also:
    - docs/waggle/gateway-contract.md section 9.4 (observation) and ADR-0054.
    - pollen.executors.observe.files for the file-system half of watch mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import psutil

from pollen.executors.info import host_capacity
from waggle.messages.swarm import NetworkCounter, ProcessSample, ResourceSample
from waggle.messages.swarm.watch import (
    MAX_INTERFACE_CHARS,
    MAX_NETWORK_INTERFACES,
    MAX_PROCESS_NAME_CHARS,
)

PROCESS_ATTRS = ["pid", "name", "cpu_percent", "memory_info", "create_time"]  # What psutil reads.

__all__ = ["collect_processes", "collect_resources", "prime_cpu"]


def prime_cpu() -> None:
    """Take the first CPU sample of every process, so the next reading has a baseline."""
    for _ in psutil.process_iter(["cpu_percent"]):
        pass


def collect_processes(max_items: int) -> tuple[tuple[ProcessSample, ...], bool]:
    """List the running processes, busiest first.

    Args:
        max_items: The most processes to return.

    Returns:
        The samples, cut at ``max_items``, and whether any process was left out.
    """
    cores = psutil.cpu_count(logical=True) or 1
    samples: list[ProcessSample] = []
    for process in psutil.process_iter(PROCESS_ATTRS):
        info = process.info
        memory = info["memory_info"]
        created = info["create_time"]
        samples.append(
            ProcessSample(
                pid=info["pid"],
                name=(info["name"] or "")[:MAX_PROCESS_NAME_CHARS],
                cpu_share=min(1.0, max(0.0, (info["cpu_percent"] or 0.0) / 100.0 / cores)),
                rss_bytes=0 if memory is None else memory.rss,
                started_at=None if created is None else datetime.fromtimestamp(created, UTC),
            )
        )
    # Busiest first, so a cut loses the least busy; memory breaks ties between idle processes.
    samples.sort(key=lambda s: (s.cpu_share, s.rss_bytes), reverse=True)
    return tuple(samples[:max_items]), len(samples) > max_items


def collect_resources(data_dir: Path) -> ResourceSample:
    """Take one full resource snapshot: load, memory, disk, uptime and network counters.

    Args:
        data_dir: The data directory; its filesystem is the disk the figures describe.

    Returns:
        The sample; the network list is cut at the wire bound.
    """
    host = host_capacity(data_dir)
    counters = psutil.net_io_counters(pernic=True) or {}
    network = tuple(
        NetworkCounter(
            interface=name[:MAX_INTERFACE_CHARS], rx_bytes=nic.bytes_recv, tx_bytes=nic.bytes_sent
        )
        for name, nic in sorted(counters.items())
        if name
    )
    return ResourceSample(
        cpu_load=host.cpu_load,
        memory_bytes=host.memory_bytes,
        memory_free_bytes=host.memory_free_bytes,
        disk_bytes=host.disk_bytes,
        disk_free_bytes=host.disk_free_bytes,
        uptime_s=max(0.0, datetime.now(UTC).timestamp() - psutil.boot_time()),
        network=_unique(network)[:MAX_NETWORK_INTERFACES],
    )


def _unique(counters: tuple[NetworkCounter, ...]) -> tuple[NetworkCounter, ...]:
    """Drop a counter whose truncated name repeats an earlier one (the wire wants unique names)."""
    seen: set[str] = set()
    kept: list[NetworkCounter] = []
    for counter in counters:
        if counter.interface not in seen:
            seen.add(counter.interface)
            kept.append(counter)
    return tuple(kept)
