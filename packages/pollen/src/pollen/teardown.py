"""End the packet that ending its service left running, and the lease programs it started.

``pollen uninstall`` removes the service unit, and on Linux and macOS the service manager stops
the Pollen Packet (the thin gateway program on a device the Hive borrows) with it. Windows'
Scheduled Task runs the packet under a console host with no window, and ending the task ends that
host, not the packet below it. This module finds that packet through the status document it
writes and ends it. Ending it by force orphans the programs it started for leases (each in a
windowless console of its own), so those are collected first and ended after it; the on-device
Warden and its own programs are not touched here, because the CLI stops the Warden gracefully so
it can hand its leases back.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.cli
    (``uninstall``); calls into pollen.agent (the status document), pollen.state, psutil and the
    standard library only.

Key invariants:
    - Only a process whose command line runs ``run`` for this very data directory is ended, so a
      recycled pid or another data directory's packet is left alone; never this process itself.
    - Nothing here prints; the CLI says when a packet could not be ended.

See Also:
    - pollen.platform.windows (``install_service``) for the task this works around.
    - docs/runbooks/enrolling-a-device.md section 6 (taking the device back).
"""

from __future__ import annotations

import contextlib
import os

import psutil

from pollen.agent import read_status
from pollen.errors import PollenError
from pollen.state import DataLayout, load_state

PACKET_END_WAIT_S = 10.0  # How long uninstall waits for the packet it ends to go.

__all__ = ["PACKET_END_WAIT_S", "end_packet"]


def end_packet(layout: DataLayout) -> int | None:
    """End the packet still running for ``layout``'s data directory, and what it left behind.

    Args:
        layout: The data directory whose packet is ended.

    Returns:
        None when no such packet runs any more; its pid when it was found and could not be ended
        (refused, or still there after PACKET_END_WAIT_S).
    """
    status = read_status(layout.status)
    if status is None:
        return None
    try:
        packet = psutil.Process(status.pid)
        argv = packet.cmdline()
        # Only this data directory's packet: a recycled pid runs something else.
        if packet.pid == os.getpid() or "run" not in argv or str(layout.root) not in argv:
            return None
        left = _lease_programs(packet, _warden_pid(layout))
        packet.terminate()
        packet.wait(PACKET_END_WAIT_S)
    except psutil.NoSuchProcess:
        return None  # Gone already.
    except psutil.Error:
        return status.pid  # Refused, or it outlived the wait.
    # Collected before the packet went: once it has, nothing links them to it any more.
    for program in left:
        with contextlib.suppress(psutil.NoSuchProcess):
            program.kill()
    return None


def _lease_programs(packet: psutil.Process, warden: int | None) -> list[psutil.Process]:
    """List what ``packet`` started, at any depth, except the Warden and what it started."""
    below = packet.children(recursive=True)
    spared = {warden} if warden is not None else set()
    for process in below:
        if process.pid == warden:
            spared.update(child.pid for child in process.children(recursive=True))
    return [process for process in below if process.pid not in spared]


def _warden_pid(layout: DataLayout) -> int | None:
    """Return the pid of the Warden the state records, or None if there is none to read."""
    try:
        record = load_state(layout.state).warden
    except PollenError:
        return None
    return None if record is None else record.pid
