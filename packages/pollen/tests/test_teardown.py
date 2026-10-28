"""Test ending the packet a removed service left running, and the lease programs it started.

Ending the Windows Scheduled Task ends the windowless console host it started, not the Pollen
Packet (the thin gateway program on a device the Hive borrows) below it, so ``pollen uninstall``
ends that packet itself (pollen.teardown). These tests stand a real program in for the packet:
its command line runs ``run`` for a data directory, and it writes its own status document's pid.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.teardown.

Key invariants:
    - Only the packet of this very data directory is ended; another directory's is left alone.
    - The lease programs a forced packet would orphan are ended too; the Warden and what it
      started are left for the CLI to stop gracefully.

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
from states import sample_state

from pollen.agent import PacketStatus, write_status
from pollen.state import DataLayout, WardenRecord, save_state
from pollen.teardown import end_packet
from waggle.clock import Clock
from waggle.messages.swarm import RuntimeLevel

_KILLED_WAIT_S = 2.0  # Long enough for a killed program to go; a live one is only waited this.
# A stand-in packet: starts two sleepers (a lease program, then the Warden), prints their pids.
_PACKET = (
    "import json, subprocess, sys, time\n"
    "sleep = [sys.executable, '-c', 'import time; time.sleep(60)']\n"
    "kids = [subprocess.Popen(sleep).pid for _ in range(2)]\n"
    "print(json.dumps(kids), flush=True)\n"
    "time.sleep(60)"
)


def _start(data_dir: Path) -> tuple[subprocess.Popen[str], list[int]]:
    """Start the stand-in packet for ``data_dir``; return it and its children's pids."""
    argv = [sys.executable, "-c", _PACKET, "run", "--data-dir", str(data_dir)]
    # SAFETY: this interpreter and fixed arguments.
    packet = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True)  # noqa: S603
    assert packet.stdout is not None
    return packet, json.loads(packet.stdout.readline())


def _record(layout: DataLayout, clock: Clock, packet: int, warden: int) -> None:
    """Write the status document of ``packet`` and a state whose Warden is ``warden``."""
    status = PacketStatus(clock).snapshot(open_leases=0, outbox_pending=0, silence_s=0.0)
    asyncio.run(write_status(layout.status, status.model_copy(update={"pid": packet})))
    created = psutil.Process(warden).create_time()
    record = WardenRecord(
        pid=warden, created=created, runtime_version="1.4.0", started_at=clock.now()
    )
    save_state(layout.state, sample_state(clock, level=RuntimeLevel.COLONIZED, warden=record))


@pytest.mark.parametrize("runs_this_directory", [True, False])
def test_this_directorys_packet_and_its_lease_programs_end_and_the_warden_is_left(
    tmp_path: Path, clock: Clock, runs_this_directory: bool
) -> None:
    layout = DataLayout(tmp_path / "data")
    layout.ensure()
    packet, (lease_program, warden) = _start(
        layout.root if runs_this_directory else tmp_path / "other"
    )
    try:
        _record(layout, clock, packet.pid, warden)

        assert end_packet(layout) is None

        assert (packet.poll() is not None) is runs_this_directory
        assert _ended(lease_program) is runs_this_directory
        assert psutil.Process(warden).is_running()  # The CLI stops it gracefully, later.
    finally:
        for pid in (lease_program, warden):
            _kill(pid)
        packet.kill()
        packet.communicate()  # Reaps it and closes its pipe.


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
