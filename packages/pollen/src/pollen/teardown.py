"""End the packet that ending its service left running, and release what it held.

``pollen uninstall`` removes the service unit, and on Linux and macOS the service manager stops
the Pollen Packet (the thin gateway program on a device the Hive borrows) with it. Windows'
Scheduled Task runs the packet under a console host with no window, and ending the task ends that
host, not the packet below it. This module finds that packet through the status document it
writes and asks it to stop the way it would at a terminal (Ctrl+C on its console, SIGTERM
elsewhere), so it releases every lease on the way out: their programs ended, their changes undone,
their scratch removed. A packet that does not go in time is forced, which skips that release, so
the release is then run here instead, over the data directory's lease table. Forcing it also
orphans the programs it started for leases (each in a windowless console of its own), so those
are collected first and ended after it. The on-device Warden and what it started are left alone
here; the CLI stops the Warden next. On Windows that stop is forced, because no break event from
the CLI's own console reaches the Warden, and the leases it held were released by the packet.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.cli
    (``uninstall``); calls into pollen.agent (the status document and the shutdown waits),
    pollen.build (a packet assembled only to release its leases), pollen.platform, pollen.state,
    psutil and the standard library only.

Key invariants:
    - Only a process whose command line runs ``run`` for this very data directory is ended, so a
      recycled pid or another data directory's packet is left alone; never this process itself.
    - The Warden is recognised by its pid and its creation time, so a recycled pid is not spared.
    - A packet that had to be forced never leaves a lease unreleased without saying so.
    - Nothing here prints; the CLI says what could not be done.

See Also:
    - pollen.platform.console for how Ctrl+C reaches a console from outside it.
    - pollen.agent.packet for the release a stopping packet does itself.
    - docs/runbooks/enrolling-a-device.md section 6 (taking the device back).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import psutil

from pollen.agent import SHUTDOWN_RELEASE_TIMEOUT_S, SHUTDOWN_WAIT_S, read_status
from pollen.build import build_packet
from pollen.errors import PollenError
from pollen.platform import PlatformShim
from pollen.state import DataLayout, WardenRecord, load_state
from waggle.clock import Clock

# How long a packet asked to stop gets: its own stop wait, then the release it does on the way out.
PACKET_END_WAIT_S = SHUTDOWN_WAIT_S + SHUTDOWN_RELEASE_TIMEOUT_S + 5.0
KILLED_WAIT_S = 5.0  # How long a forced packet gets to be gone.
_CREATE_TIME_TOLERANCE_S = 1.0  # As the shims': psutil's creation time is stable per process.

__all__ = ["KILLED_WAIT_S", "PACKET_END_WAIT_S", "Ending", "end_packet"]


@dataclass(frozen=True, slots=True)
class Ending:
    """What ending the packet left undone, for the CLI to say."""

    stuck: int | None = None  # The packet's pid, when it could not be ended at all.
    survivors: tuple[int, ...] = ()  # Lease programs it started that could not be killed.
    is_unreleased: bool = False  # It was forced and its leases could not be released after it.


async def end_packet(
    layout: DataLayout, shim: PlatformShim, clock: Clock, wait_s: float = PACKET_END_WAIT_S
) -> Ending:
    """Stop the packet still running for ``layout``'s data directory, and release what it held.

    Args:
        layout: The data directory whose packet is ended.
        shim: Asks the packet to stop; also used by the release after a forced end.
        clock: The clock that release runs with.
        wait_s: How long the packet gets to stop by itself before it is forced.

    Returns:
        What could not be done; an empty ``Ending`` when nothing is left.

    Raises:
        Nothing: every failure to end a process is reported in the result instead.
    """
    status = read_status(layout.status)
    if status is None:
        return Ending()
    try:
        packet = psutil.Process(status.pid)
        argv = packet.cmdline()
        # Only this data directory's packet: a recycled pid runs something else.
        if packet.pid == os.getpid() or "run" not in argv or str(layout.root) not in argv:
            return Ending()
        left = _lease_programs(packet, _warden(layout))
    except psutil.NoSuchProcess:
        return Ending()  # Gone already.
    except psutil.Error:
        return Ending(stuck=status.pid)
    if shim.ask_to_stop(packet.pid) and await _gone(packet, wait_s):
        return Ending(survivors=_kill_all(left))  # It released its own leases on the way out.
    try:
        packet.kill()
    except psutil.NoSuchProcess:
        pass  # It went just as the wait ran out.
    except psutil.Error:
        return Ending(stuck=status.pid)
    if not await _gone(packet, KILLED_WAIT_S):
        return Ending(stuck=status.pid)
    # Collected before the packet went: once it has, nothing links them to it any more.
    survivors = _kill_all(left)
    return Ending(survivors=survivors, is_unreleased=not await _release(layout, shim, clock))


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
    """Release every lease the forced packet left in its table, as its own stop would have."""
    try:
        await build_packet(layout, shim, clock).sweep()
    except PollenError:
        return False  # The state or the table cannot be read: nothing to release them with.
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
