"""Test ending the packet a removed service left running, and releasing what it held.

Ending the Windows Scheduled Task ends the windowless console host it started, not the Pollen
Packet (the thin gateway program on a device the Hive borrows) below it, so ``pollen uninstall``
ends that packet itself (pollen.teardown): it asks first (Ctrl+C on the packet's console on
Windows, SIGTERM elsewhere) and forces it only if it does not go, then releases the leases the
forced packet could not. These tests stand a real program in for the packet: its command line
runs ``run`` for a data directory, it starts a lease program and a Warden (which starts a child of
its own), and it either honours the request or ignores it. On Windows it runs in a windowless
console of its own, as the real one does, so the request never reaches the test's console.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.teardown.

Key invariants:
    - Only the packet of this very data directory is ended; another directory's is left alone.
    - A packet that ignores the request is forced, and its leases are released after it: the
      lease's scratch is gone.
    - The lease programs a forced packet would orphan are ended; the Warden and what it started
      are left for the CLI, which forces the Warden on Windows (no break event reaches it there).

See Also:
    - packages/pollen/src/pollen/teardown.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
from pathlib import Path

import psutil
import pytest
from device_kit import Device

from pollen.agent import PacketStatus, write_status
from pollen.lease import DeviceLease, LeaseTable
from pollen.platform import select_shim
from pollen.state import WardenRecord, save_state
from pollen.teardown import end_packet
from waggle.clock import Clock
from waggle.ids import new_lease_id
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import RuntimeLevel

ASKED_EXIT = 7  # What the stand-in exits with when it honours the request to stop.
_KILLED_WAIT_S = 2.0  # Long enough for a killed program to go; a live one is only waited this.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # A console of its own, as the packet has.
# A stand-in packet: starts a lease program and a Warden with a child of its own, prints the three
# pids, then sleeps; argv[1] says whether it honours a request to stop (Ctrl+C or SIGTERM).
_PACKET = """
import ctypes, json, signal, subprocess, sys, time
honours = sys.argv[1] == "honours"
def asked(*_):
    sys.exit(7)
for name in ("SIGINT", "SIGTERM"):
    signal.signal(getattr(signal, name), asked if honours else signal.SIG_IGN)
if honours and sys.platform == "win32":
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
code = "import subprocess, time; c = subprocess.Popen(%r); print(c.pid, flush=True); time.sleep(60)"
# As the real packet starts them: a lease program in a console of its own, the Warden sharing
# the packet's console in a process group of its own, which Ctrl+C there does not reach.
lease = subprocess.Popen(sleep, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
argv = [sys.executable, "-c", code % sleep]
warden = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, creationflags=group)
grandchild = int(warden.stdout.readline())
print(json.dumps([lease.pid, warden.pid, grandchild]), flush=True)
time.sleep(60)
"""


def _start(data_dir: Path, honours: bool) -> tuple[subprocess.Popen[str], list[int]]:
    """Start the stand-in packet for ``data_dir``; return it and its descendants' pids."""
    mode = "honours" if honours else "ignores"
    argv = [sys.executable, "-c", _PACKET, mode, "run", "--data-dir", str(data_dir)]
    # SAFETY: this interpreter and fixed arguments.
    packet = subprocess.Popen(  # noqa: S603
        argv, stdout=subprocess.PIPE, text=True, creationflags=_NO_WINDOW
    )
    assert packet.stdout is not None
    return packet, json.loads(packet.stdout.readline())


async def _record(device: Device, clock: Clock, packet: int, warden: int) -> Path:
    """Write the packet's status, a Warden record and one open lease; return its scratch."""
    layout = device.layout
    status = PacketStatus(clock).snapshot(open_leases=1, outbox_pending=0, silence_s=0.0)
    await write_status(layout.status, status.model_copy(update={"pid": packet}))
    created = psutil.Process(warden).create_time()
    record = WardenRecord(
        pid=warden, created=created, runtime_version="1.4.0", started_at=clock.now()
    )
    colonized = {"level": RuntimeLevel.COLONIZED, "warden": record}
    save_state(layout.state, device.state.model_copy(update=colonized))
    scratch = layout.scratch(new_lease_id(clock))
    scratch.mkdir(parents=True)
    (scratch / "leftover.txt").write_text("x")
    lease = DeviceLease(
        lease_id=scratch.name,
        cell_id=device.state.cell_id,
        holder=device.state.warden_id,
        access_level=AccessLevel.FULL,
        scratch_root=str(scratch),
        quota_bytes=1000,
        opened_at=clock.now(),
    )
    await LeaseTable(layout.lease_table).put(lease)
    return scratch


@pytest.mark.parametrize(
    ("this_directory", "honours"),
    [
        (True, False),  # Ignores the request: forced, lease programs ended, leases released.
        (True, True),  # Honours it: it goes by itself, as it would at a terminal.
        (False, False),  # Another data directory's packet: nothing is touched.
    ],
)
async def test_this_directorys_packet_ends_and_releases_and_the_warden_is_left(
    device: Device, clock: Clock, this_directory: bool, honours: bool
) -> None:
    layout = device.layout
    data_dir = layout.root if this_directory else layout.root.parent / "other"
    packet, (lease_program, warden, warden_child) = _start(data_dir, honours)
    try:
        scratch = await _record(device, clock, packet.pid, warden)

        ended = await end_packet(layout, select_shim(), clock, wait_s=5.0 if honours else 1.0)

        assert ended.stuck is None and ended.survivors == () and not ended.is_unreleased
        returncode = await asyncio.to_thread(_returncode, packet)
        assert (returncode is not None) is this_directory
        assert (returncode == ASKED_EXIT) is (this_directory and honours)
        assert _ended(lease_program) is this_directory
        assert scratch.exists() is not (this_directory and not honours)  # Released after a force.
        # The Warden and its own child are the CLI's to stop next.
        assert psutil.Process(warden).is_running() and psutil.Process(warden_child).is_running()
    finally:
        for pid in (lease_program, warden_child, warden):
            _kill(pid)
        packet.kill()
        packet.communicate()  # Reaps it and closes its pipe.


def _returncode(packet: subprocess.Popen[str]) -> int | None:
    """Return the stand-in's exit status if it has ended within a moment, else None."""
    try:
        return packet.wait(_KILLED_WAIT_S)
    except subprocess.TimeoutExpired:
        return None


def _ended(pid: int) -> bool:
    """Say whether ``pid`` ends within a moment; a killed program goes at once."""
    try:
        psutil.Process(pid).wait(_KILLED_WAIT_S)
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        pass
    return True


def _kill(pid: int) -> None:
    """End a sleeper a test started, if it still runs."""
    with contextlib.suppress(psutil.NoSuchProcess):
        psutil.Process(pid).kill()
