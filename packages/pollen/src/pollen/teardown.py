"""End the packet that ending its service left running, and release what it held.

``pollen uninstall`` removes the service unit, and on Linux and macOS the service manager stops
the Pollen Packet (the thin gateway program on a device the Hive borrows) with it. Windows'
Scheduled Task runs the packet under a console host with no window, and ending the task ends that
host, not the packet below it. This module finds that packet through the status document it
writes and asks it to stop the way it would at a terminal (Ctrl+C on its console, SIGTERM
elsewhere), so it releases every lease on the way out: their programs ended, their changes undone,
their scratch removed. A packet that does not go in time is forced, which skips that release, and
a packet that crashed long ago never ran it, so whenever leases are still in the data directory's
table afterwards the release is run here instead, and read back from the table. Forcing it also
orphans the programs it started for leases (nothing links them to it once it is gone), so those
are collected first and ended after it. The on-device Warden and what it started are left alone
here; the CLI stops the Warden next, asking first (on Windows with Ctrl+Break, through the same
helper, on its own console) and forcing it only if it does not go.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.cli
    (``uninstall``); calls into pollen.agent (the status document and the shutdown waits),
    pollen.build (a packet assembled only to release its leases), pollen.platform, pollen.state,
    psutil and the standard library only.

Key invariants:
    - Only a process whose command line runs ``run`` for this very data directory (however it is
      spelled) is ended, so a recycled pid or another data directory's packet is left alone; never
      this process itself. A packet with no status document yet is still found.
    - The Warden is recognised by its pid and its creation time, so a recycled pid is not spared.
    - No lease is left in the table without saying so, whether a packet ran or not.
    - Nothing here prints; the CLI says what could not be done.

See Also:
    - pollen.platform.console for how Ctrl+C reaches a console from outside it.
    - pollen.agent.packet for the release a stopping packet does itself.
    - docs/runbooks/enrolling-a-device.md section 6 (taking the device back).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass

import psutil

from pollen.agent import SHUTDOWN_RELEASE_TIMEOUT_S, SHUTDOWN_WAIT_S, read_status
from pollen.build import build_packet
from pollen.errors import PollenError
from pollen.lease import LeaseTable
from pollen.platform import PlatformShim
from pollen.state import DataLayout, WardenRecord, load_state
from waggle.clock import Clock

PACKET_END_MARGIN_S = 5.0  # For the packet's process to exit once its own release has finished.
# How long a packet asked to stop gets: its own stop wait, then the release it does on the way out.
PACKET_END_WAIT_S = SHUTDOWN_WAIT_S + SHUTDOWN_RELEASE_TIMEOUT_S + PACKET_END_MARGIN_S
KILLED_WAIT_S = 5.0  # How long a forced packet gets to be gone.
_CREATE_TIME_TOLERANCE_S = 1.0  # As the shims': psutil's creation time is stable per process.

__all__ = ["KILLED_WAIT_S", "PACKET_END_MARGIN_S", "PACKET_END_WAIT_S", "Ending", "end_packet"]


@dataclass(frozen=True, slots=True)
class Ending:
    """What ending the packet left undone, for the CLI to say."""

    stuck: int | None = None  # The packet's pid, when it could not be ended at all.
    survivors: tuple[int, ...] = ()  # Lease programs it started that could not be killed.
    is_unreleased: bool = False  # Leases are still in the table after the release was run.


async def end_packet(
    layout: DataLayout, shim: PlatformShim, clock: Clock, wait_s: float = PACKET_END_WAIT_S
) -> Ending:
    """Stop the packet still running for ``layout``'s data directory, and release what it held.

    Args:
        layout: The data directory whose packet is ended.
        shim: Asks the packet to stop; also used by the release of what it left.
        clock: The clock that release runs with.
        wait_s: How long the packet gets to stop by itself before it is forced.

    Returns:
        What could not be done; an empty ``Ending`` when nothing is left.

    Raises:
        Nothing: every failure to end a process is reported in the result instead.
    """
    survivors: tuple[int, ...] = ()
    packet = _running_packet(layout)
    if packet is not None:
        try:
            left = _lease_programs(packet, _warden(layout))
        except psutil.NoSuchProcess:
            left = []  # It went by itself meanwhile.
        except psutil.Error:
            return Ending(stuck=packet.pid)
        if not await _stop(packet, shim, wait_s):
            return Ending(stuck=packet.pid)
        # Collected before the packet went: once it has, nothing links them to it any more.
        survivors = _kill_all(left)
    # Whatever ended the packet (asked, forced, a crash long ago), no lease may stay held.
    return Ending(survivors=survivors, is_unreleased=not await _release(layout, shim, clock))


def _running_packet(layout: DataLayout) -> psutil.Process | None:
    """Return this data directory's packet if it runs; else None.

    The status document names it once it has written one; until then (it just started) every
    process is looked at. A packet run through a launcher (a virtual environment's python.exe
    starts the real interpreter below it) matches twice, and the one below is the packet.
    """
    status = read_status(layout.status)
    if status is not None:
        with contextlib.suppress(psutil.Error):  # Gone, or unreadable: look at every process.
            named = psutil.Process(status.pid)
            if _runs_packet(named, layout):
                return named
    matches = [process for process in psutil.process_iter() if _runs_packet(process, layout)]
    pids = {process.pid for process in matches}
    for process in matches:
        with contextlib.suppress(psutil.Error):
            if not any(child.pid in pids for child in process.children(recursive=True)):
                return process
    return None


def _runs_packet(process: psutil.Process, layout: DataLayout) -> bool:
    """Say whether ``process`` runs ``pollen run`` for this data directory (never this process)."""
    try:
        argv = process.cmdline()
    except psutil.Error:
        return False  # Gone, or not readable as ours.
    # However the directory was spelled: relative, another case on Windows, a trailing dot.
    root = _canonical(str(layout.root))
    return process.pid != os.getpid() and "run" in argv and any(_canonical(a) == root for a in argv)


def _canonical(path: str) -> str:
    """Return ``path`` absolute and in the platform's case, for comparing two spellings."""
    return os.path.normcase(os.path.abspath(path))


async def _stop(packet: psutil.Process, shim: PlatformShim, wait_s: float) -> bool:
    """Ask ``packet`` to stop, force it if it does not within ``wait_s``; True once it is gone."""
    if shim.ask_to_stop(packet.pid) and await _gone(packet, wait_s):
        return True  # It released its own leases on the way out.
    try:
        packet.kill()
    except psutil.NoSuchProcess:
        return True  # It went just as the wait ran out.
    except psutil.Error:
        return False
    return await _gone(packet, KILLED_WAIT_S)


async def _gone(packet: psutil.Process, wait_s: float) -> bool:
    """Wait up to ``wait_s`` for ``packet`` to end; True if it has."""
    try:
        await asyncio.to_thread(packet.wait, wait_s)
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        pass
    return True


async def _release(layout: DataLayout, shim: PlatformShim, clock: Clock) -> bool:
    """Release every lease left in the table, as the packet's own stop does; True if none is left.

    The packet's release swallows its own failures, so success is read back from the table.
    """
    if not _leases_left(layout):
        return True
    with contextlib.suppress(PollenError):  # State or table unreadable: checked just below.
        await build_packet(layout, shim, clock).sweep()
    return not _leases_left(layout)


def _leases_left(layout: DataLayout) -> bool:
    """Say whether the lease table still holds a lease; True when it cannot be read."""
    try:
        return bool(LeaseTable.load(layout.lease_table).all())
    except PollenError:
        return True


def _kill_all(programs: list[psutil.Process]) -> tuple[int, ...]:
    """Kill each of ``programs``; return the pids of those that could not be killed."""
    survivors: list[int] = []
    for program in programs:
        try:
            program.kill()
        except psutil.NoSuchProcess:
            continue  # Ended already, perhaps by the packet's own release.
        except psutil.Error:
            survivors.append(program.pid)
    return tuple(survivors)


def _lease_programs(packet: psutil.Process, warden: WardenRecord | None) -> list[psutil.Process]:
    """List what ``packet`` started, at any depth, except the Warden and what it started."""
    below = packet.children(recursive=True)
    spared: set[int] = set()
    for process in below:
        if warden is not None and _is_warden(process, warden):
            spared.add(process.pid)
            spared.update(child.pid for child in process.children(recursive=True))
    return [process for process in below if process.pid not in spared]


def _is_warden(process: psutil.Process, warden: WardenRecord) -> bool:
    """Say whether ``process`` is the recorded Warden: its pid and its creation time."""
    if process.pid != warden.pid:
        return False
    try:
        return abs(process.create_time() - warden.created) <= _CREATE_TIME_TOLERANCE_S
    except psutil.Error:
        return False


def _warden(layout: DataLayout) -> WardenRecord | None:
    """Return the Warden the state records, or None if there is none to read."""
    try:
        return load_state(layout.state).warden
    except PollenError:
        return None
