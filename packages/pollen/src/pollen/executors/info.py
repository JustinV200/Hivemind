"""Report what the device is: platform, capabilities and host capacity, as the wire carries them.

Everything the Hive later decides about a device (placement, tool eligibility, whether it can be
colonized) reads three reports the Pollen Packet (the thin gateway program on a device the Hive
borrows) makes about its own machine: the ``PlatformReport`` (operating system, distribution,
architecture, package manager, shell, Python), the ``CellCapabilitiesReport`` (display, audio,
browser) and the ``HostCapacityReport`` (cores, memory, disk, load). They travel once in the
enrolment request, and capacity travels again in ``forage.capacity_report`` on every reconnection,
when a figure changes materially and on a timer (a Cell is a unit of compute; Forage is the
Hive's word for its capacity). Every reading is best effort and conservative: a figure that
cannot be read is zero, never a generous guess. A gateway hosts no models and runs no bees, so a
capacity report has no model servers and a usage of all zeros; a device that cannot speak for its
network reports no network scopes.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.enrol.request
    (the enrolment report) and pollen.agent.heartbeat (the capacity reports); asks the platform
    shim for the operating-system facts; calls into psutil, waggle and the standard library only.

Key invariants:
    - A report is a full snapshot, so one replayed from an outbox is self-contained.
    - Free figures never exceed their totals (the wire models would refuse them).
    - A capacity change is "material" only past a threshold, so a report is not sent for noise.

See Also:
    - docs/waggle/enrolment.md section 4 (the device prepares) for the three reports.
    - docs/waggle/gateway-contract.md section 8 for when a capacity report is sent.
    - packages/hivemind/src/hivemind/cell/local/probe.py for the Hive Stand's own probe.
"""

from __future__ import annotations

import platform
import socket
import sys
from importlib import metadata
from pathlib import Path

import psutil

from pollen.platform.base import PlatformShim
from waggle.ids import CellId
from waggle.messages.base import MAX_SUB_BEES_ON_WIRE
from waggle.messages.forage import CapacityReport, CapacityTrigger, LocalPoolUsage
from waggle.messages.reports import CellCapabilitiesReport, HostCapacityReport, PlatformReport

FALLBACK_VERSION = "0.1.0.dev0"  # Used when package metadata is absent (a single-file build).
MAX_VERSION_CHARS = 32  # The wire bound of EnrolRequest.runtime_version.
MAX_HOSTNAME_CHARS = 253  # The wire bound of EnrolRequest.hostname.
DEFAULT_MAX_SUB_BEES = 4  # The Hive Stand's own conservative default: do not overrun the machine.
MATERIAL_FRACTION = 0.2  # Free memory or disk moving by a fifth of the total is worth a report.

__all__ = [
    "build_capacity_report",
    "capabilities_report",
    "capacity_changed",
    "host_capacity",
    "hostname",
    "platform_report",
    "runtime_version",
]


def runtime_version() -> str:
    """Return the installed pollen version, or a fallback when metadata is missing."""
    try:
        return metadata.version("pollen")[:MAX_VERSION_CHARS]
    except metadata.PackageNotFoundError:
        return FALLBACK_VERSION


def hostname() -> str:
    """Return the device's hostname, trimmed to the wire bound; text the operator never trusts."""
    return socket.gethostname()[:MAX_HOSTNAME_CHARS]


def platform_report(shim: PlatformShim) -> PlatformReport:
    """Report the operating system, distribution, architecture, shell and Python.

    Args:
        shim: The platform shim, which knows the operating-system facts.

    Returns:
        The report every later decision about the device reads.
    """
    facts = shim.facts()
    # A single-file build bundles an interpreter no tool on the device can reach, so it is not
    # "the Python available to tools".
    python = None if getattr(sys, "frozen", False) else platform.python_version()
    return PlatformReport(
        os=facts.os_family,
        distribution=facts.distribution[:64] if facts.distribution else None,
        architecture=facts.architecture[:32],
        package_manager=facts.package_manager,
        shell=facts.shell[:64],
        python_version=python,
    )


def capabilities_report(shim: PlatformShim) -> CellCapabilitiesReport:
    """Report what the device can do: display, audio, browser; never a network scope.

    Args:
        shim: The platform shim, which knows the operating-system facts.

    Returns:
        The capability flags. ``can_host_model`` and ``real_display_allowed`` are false: a probe
        cannot know whether a model server is installed, and driving the operator's own screen is
        an opt-in only the operator can give.
    """
    facts = shim.facts()
    return CellCapabilitiesReport(
        has_display=facts.has_display,
        has_audio=facts.has_audio,
        has_browser=facts.has_browser,
        can_start_display=facts.can_start_display,
        can_host_model=False,
        real_display_allowed=False,
        network_scopes=(),
    )


def host_capacity(data_dir: Path) -> HostCapacityReport:
    """Measure cores, memory, disk and load right now.

    Args:
        data_dir: The data directory; its filesystem is the disk "where the Hive works".

    Returns:
        A full snapshot; anything unreadable is reported as zero.
    """
    cores = psutil.cpu_count(logical=True) or 1
    memory = psutil.virtual_memory()
    disk_total, disk_free = _disk_bytes(data_dir)
    return HostCapacityReport(
        cores=cores,
        memory_bytes=memory.total,
        memory_free_bytes=min(memory.available, memory.total),
        disk_bytes=disk_total,
        disk_free_bytes=disk_free,
        cpu_load=max(0.0, psutil.getloadavg()[0] / cores),
        gpus=(),  # GPU enumeration belongs to the Nuc work; a gateway reports none.
    )


def _disk_bytes(path: Path) -> tuple[int, int]:
    """Return total and free bytes of the filesystem holding ``path`` (or its nearest parent)."""
    probe = path
    # The data directory may not exist yet (enrolment measures before creating it): walk up to
    # something that does, which lives on the same filesystem in every case that matters.
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = psutil.disk_usage(str(probe))
    except OSError:
        return 0, 0
    return usage.total, min(usage.free, usage.total)


def build_capacity_report(
    cell_id: CellId, trigger: CapacityTrigger, host: HostCapacityReport
) -> CapacityReport:
    """Wrap a host snapshot as a ``forage.capacity_report`` payload.

    Args:
        cell_id: The Cell this device is.
        trigger: Why the report is sent.
        host: The snapshot to carry.

    Returns:
        A report with no model servers and a usage of all zeros: a gateway hosts no models and
        runs no bees.
    """
    return CapacityReport(
        cell_id=cell_id,
        trigger=trigger,
        host=host,
        model_servers=(),
        max_sub_bees=max(1, min(host.cores, DEFAULT_MAX_SUB_BEES, MAX_SUB_BEES_ON_WIRE)),
        usage=LocalPoolUsage(
            sub_bees_active=0, model_vram_bytes=0, model_disk_bytes=0, seats_exported=0
        ),
    )


def capacity_changed(previous: HostCapacityReport, current: HostCapacityReport) -> bool:
    """Say whether ``current`` differs from ``previous`` enough to report.

    Args:
        previous: The snapshot last reported.
        current: The snapshot just measured.

    Returns:
        True if cores, total memory or total disk changed, or free memory or free disk moved by
        at least MATERIAL_FRACTION of its total. Load is volatile and never counts alone.
    """
    if (previous.cores, previous.memory_bytes, previous.disk_bytes) != (
        current.cores,
        current.memory_bytes,
        current.disk_bytes,
    ):
        return True
    memory_move = abs(current.memory_free_bytes - previous.memory_free_bytes)
    disk_move = abs(current.disk_free_bytes - previous.disk_free_bytes)
    return (
        memory_move >= MATERIAL_FRACTION * current.memory_bytes
        or disk_move >= MATERIAL_FRACTION * current.disk_bytes
    )
