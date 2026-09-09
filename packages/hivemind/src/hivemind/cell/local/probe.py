"""Probe the running host's platform, capabilities and capacity, POSIX and Windows side by side.

The Hive Stand (the machine the Queen runs on) needs to describe itself as a Cell without any
help from the operator: `probe_host` reads what the standard library can tell us about this
process's own machine -- OS family, architecture, cores, memory, disk, a coarse guess at display
and audio and browser presence -- and turns it into the same `CellCapabilities` and
`ForageCapacity` every other Cell source reports. Every reading is best effort: a figure this
process cannot determine on this platform falls back to a conservative value (unknown memory is
0 bytes free, not the whole machine; unknown display is `False`, not `True`) rather than guessing
generously, because placement and Forage allocation both read these numbers to decide what is
safe to run here. `ProbeError` (already defined in `hivemind.cell.errors`) is the one case this
module refuses to guess through: a host that reports zero usable cores is unusable, not merely
under-specified.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Called by
    `hivemind.cell.local.source.HiveStandSource.cells()` once at construction (the static half)
    and again on every call (the live half, through `refresh_live`). Calls into the standard
    library only (`os`, `platform`, `shutil`, `ctypes` on Windows), plus `hivemind.cell.needs`,
    `hivemind.cell.local.config` and `hivemind.forage`.

Key invariants:
    - `probe_host` raises `ProbeError` only when this process cannot determine any usable core
      count at all; every other figure degrades to a conservative value instead of raising.
    - `HiveStandConfig.cores`, `.memory_bytes` and `.max_sub_bees` (the manifest's own overrides)
      always win over whatever this module would otherwise probe.
    - `can_start_display` and `can_host_model` are always False: neither capability exists yet
      (roadmap steps for the Exoskeleton and local model hosting land in later phases).

See Also:
    - .claude/roadmap.md step 3.11 for "probe.py (platform, arch, cores, memory, GPU, display,
      browser, with POSIX and Windows shims side by side)."
    - docs/adr/0010-cells-are-real-or-virtual-terminal-first.md for why the Hive Stand's probe is
      standard-library only.
    - hivemind.cell.models for CellCapabilities, one half of this module's result.
    - hivemind.forage for ForageCapacity and HostCapacity, the other half.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell.errors import ProbeError
from hivemind.cell.local.config import HiveStandConfig
from hivemind.cell.models import CellCapabilities
from hivemind.cell.needs import OsFamily
from hivemind.forage import ForageCapacity, HostCapacity
from waggle.messages.labels import OsFamily as WireOsFamily

DEFAULT_MAX_SUB_BEES = 4  # Conservative absent an override: don't overrun the operator's machine.
_MAX_DISK_PROBE_ANCESTORS = 32  # A generous bound; a filesystem root always terminates the walk.
_POSIX_BROWSERS = ("firefox", "google-chrome", "chromium", "chromium-browser")
_POSIX_PACKAGE_MANAGERS = ("apt", "dnf", "yum", "pacman", "apk", "brew")

__all__ = ["DEFAULT_MAX_SUB_BEES", "ProbeResult", "probe_host", "refresh_live"]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class ProbeResult(BaseModel):
    """What `probe_host` found: the Hive Stand's capabilities and its Forage capacity report."""

    model_config = _MODEL_CONFIG

    capabilities: CellCapabilities = Field(description="What this machine is and can do.")
    capacity: ForageCapacity = Field(description="This machine's Forage report.")


def probe_host(config: HiveStandConfig) -> ProbeResult:
    """Probe this host once, applying `config`'s overrides where it has any.

    Args:
        config: The Hive Stand's own settings; `cores`, `memory_bytes` and `max_sub_bees` here
            win over whatever is probed.

    Returns:
        A ProbeResult combining what this module could determine with the manifest's overrides.

    Raises:
        ProbeError: This process could not determine any usable core count for this host.
    """
    cores = config.cores if config.cores is not None else _probe_cores()
    memory_total, memory_free = _memory_bytes()
    if config.memory_bytes is not None:
        memory_total = config.memory_bytes
        memory_free = min(memory_free, memory_total)
    disk_total, disk_free = _disk_bytes(config.scratch_root)
    host = HostCapacity(
        cores=cores,
        memory_bytes=memory_total,
        memory_free_bytes=memory_free,
        disk_bytes=disk_total,
        disk_free_bytes=disk_free,
        cpu_load=_cpu_load(cores),
        gpus=(),  # Best effort: no stdlib-only way to enumerate GPUs across POSIX and Windows.
        arch=platform.machine() or "unknown",
        os=_wire_os_family(),
    )
    max_sub_bees = config.max_sub_bees if config.max_sub_bees is not None else DEFAULT_MAX_SUB_BEES
    capacity = ForageCapacity(
        host=host,
        local_seats=(),  # v0: the Hive Stand hosts no model server (can_host_model=False above).
        max_sub_bees=max_sub_bees,
    )
    return ProbeResult(capabilities=_probe_capabilities(), capacity=capacity)


def refresh_live(config: HiveStandConfig, capacity: ForageCapacity) -> ForageCapacity:
    """Recompute only the figures that change moment to moment, keeping the rest as probed.

    Cores, total memory, total disk, architecture and OS rarely or never change while a process
    runs; free memory, free disk and load do. Called on every `HiveStandSource.cells()` so a
    caller sees current headroom without re-detecting the whole host each time.

    Args:
        config: The Hive Stand's own settings, for the scratch root disk usage is read from.
        capacity: A previously probed ForageCapacity, for its static totals.

    Returns:
        `capacity` with `memory_free_bytes`, `disk_free_bytes` and `cpu_load` refreshed; every
        other field is carried over unchanged.
    """
    _, memory_free = _memory_bytes()
    memory_free = min(memory_free, capacity.host.memory_bytes)
    _, disk_free = _disk_bytes(config.scratch_root)
    host = capacity.host.model_copy(
        update={
            "memory_free_bytes": memory_free,
            "disk_free_bytes": disk_free,
            "cpu_load": _cpu_load(capacity.host.cores),
        }
    )
    return capacity.model_copy(update={"host": host})


# ──────────────────────────────────────────────────────────────────────────────
# Static facts: cores, OS family, capabilities
# ──────────────────────────────────────────────────────────────────────────────


def _probe_cores() -> int:
    """Return the logical core count, or raise ProbeError if none can be determined."""
    cores = os.cpu_count()
    if not cores:
        # A host this process cannot even size is too unusable to describe as a Cell at all.
        raise ProbeError("os.cpu_count() returned no usable core count")
    return cores


def _wire_os_family() -> WireOsFamily:
    """Return the waggle wire OsFamily for this host, the type `HostCapacity.os` carries."""
    return WireOsFamily(_os_family_value())


def _os_family_value() -> str:
    """Return "LINUX", "WINDOWS" or "MACOS" for the running host, both enums' shared member name."""
    if sys.platform == "win32":
        return "WINDOWS"
    elif sys.platform == "darwin":
        return "MACOS"
    else:
        return "LINUX"


def _probe_capabilities() -> CellCapabilities:
    """Build a CellCapabilities for this host, POSIX and Windows shims side by side."""
    if sys.platform == "win32":
        return _capabilities_windows()
    else:
        return _capabilities_posix()


def _capabilities_posix() -> CellCapabilities:
    """Best-effort CellCapabilities on Linux or macOS."""
    system = platform.system()
    return CellCapabilities(
        os=OsFamily(_os_family_value()),
        arch=platform.machine() or "unknown",
        distribution=_posix_distribution(system),
        shell=os.environ.get("SHELL", "/bin/sh"),
        package_manager=next((p for p in _POSIX_PACKAGE_MANAGERS if shutil.which(p)), None),
        python_version=platform.python_version(),
        has_display=bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        has_audio=Path("/dev/snd").exists() if system == "Linux" else False,
        has_browser=any(shutil.which(name) for name in _POSIX_BROWSERS),
        can_start_display=False,  # v0: the Exoskeleton starts one on demand in a later phase.
        can_host_model=False,  # v0: local model hosting is a later phase.
        network_scopes=(),
    )


def _posix_distribution(system: str) -> str | None:
    """Return a distribution label for Linux (os-release) or macOS (platform), else None.

    Args:
        system: `platform.system()`'s own answer ("Linux" or "Darwin").
    """
    if system == "Darwin":
        version = platform.mac_ver()[0]
        return f"macOS {version}" if version else None
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return None
    for line in os_release.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.removeprefix("PRETTY_NAME=").strip('"')
    return None


def _capabilities_windows() -> CellCapabilities:
    """Best-effort CellCapabilities on Windows."""
    # mypy's platform narrowing only accepts `platform.win32_edition` inside a matching
    # `if sys.platform == "win32":` branch in *this* function -- the caller's own check
    # (`_probe_capabilities`) does not carry over -- so this repeats it, even though this
    # function is in fact only ever called from that already-guarded call site.
    distribution = platform.win32_edition() if sys.platform == "win32" else None
    return CellCapabilities(
        os=OsFamily.WINDOWS,
        arch=platform.machine() or "unknown",
        distribution=distribution,
        shell=os.environ.get("COMSPEC", "cmd.exe"),
        package_manager=next((p for p in ("winget", "choco") if shutil.which(p)), None),
        python_version=platform.python_version(),
        # Best effort: the Hive Stand is normally an operator's own interactive desktop, and
        # Microsoft Edge ships with every supported Windows release regardless of PATH contents,
        # so a shutil.which browser search (unreliable here) is not worth the false negatives.
        has_display=True,
        has_audio=True,
        has_browser=True,
        can_start_display=False,
        can_host_model=False,
        network_scopes=(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Live facts: memory, disk, load
# ──────────────────────────────────────────────────────────────────────────────


class _MemoryStatusEx(ctypes.Structure):
    """The Win32 MEMORYSTATUSEX struct; only the two fields this module reads are documented."""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_uint64),  # Total physical memory, in bytes.
        ("ullAvailPhys", ctypes.c_uint64),  # Physical memory currently available, in bytes.
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("sullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _memory_bytes() -> tuple[int, int]:
    """Return (total, free) memory in bytes, POSIX and Windows shims side by side.

    The two shims live inside one function's if/else, not as separate top-level functions:
    mypy's platform narrowing only exempts `ctypes.windll`/`os.sysconf` from attribute-existence
    checking inside a matching `if sys.platform == ...:` branch of *the function that reads
    them* -- a helper called from an already-guarded branch is analysed on its own and would
    still fail on whichever platform's mypy run does not natively have that attribute.
    """
    if sys.platform == "win32":
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        # SAFETY: a stdlib ctypes call into a documented, side-effect-free Win32 query API; the
        # struct's dwLength must be pre-filled exactly as done above or the call fails.
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return 0, 0
        return status.ullTotalPhys, status.ullAvailPhys
    else:
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            total_pages = os.sysconf("SC_PHYS_PAGES")
            avail_pages = os.sysconf("SC_AVPHYS_PAGES")
        except (ValueError, OSError):
            # Unknown -> conservative: report nothing, rather than guess a figure that could be
            # wrong in either direction for a memory-allocation decision.
            return 0, 0
        return page_size * total_pages, page_size * avail_pages


def _disk_bytes(scratch_root: Path) -> tuple[int, int]:
    """Return (total, free) bytes on the filesystem holding `scratch_root`, or its nearest parent.

    `scratch_root` may not exist yet the first time this runs; each ancestor is tried in turn so
    the probe still answers rather than raising on a directory nobody has created yet.
    """
    candidate = scratch_root
    for _ in range(_MAX_DISK_PROBE_ANCESTORS):
        try:
            usage = shutil.disk_usage(candidate)
        except OSError:
            parent = candidate.parent
            if parent == candidate:
                break  # Reached the filesystem root; nothing higher left to try.
            candidate = parent
            continue
        return usage.total, usage.free
    return 0, 0


def _cpu_load(cores: int) -> float:
    """Return the one-minute load average divided by cores; 0.0 where this host offers none."""
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None or cores <= 0:
        # Windows offers no equivalent of getloadavg without an extra dependency; 0.0 is the
        # documented best-effort answer rather than a guess.
        return 0.0
    try:
        return float(getloadavg()[0]) / cores
    except OSError:
        return 0.0
