"""Test ending the packet a removed service left running, and releasing what it held.

Removing the Windows Scheduled Task leaves the Pollen Packet (the thin gateway program on a device
the Hive borrows) running under its windowless console host, so ``pollen uninstall`` ends that
packet itself (pollen.teardown): it asks first (Ctrl+C on the packet's console on Windows, SIGTERM
elsewhere) and forces it only if it does not go, then releases whatever leases are still in the
data directory's table, whether a packet ran or not. These tests stand a real program in for the
packet: its command line runs ``run`` for a data directory, it starts a lease program and a Warden
(which starts a child of its own), writes their pids to a file, and either honours the request or
ignores it. On Windows it runs in a windowless console of its own, as the real one does, so the
request never reaches the test's console; one test runs it under ``conhost.exe --headless``
exactly as the task does, through the real ``pollen uninstall``.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.teardown (and its call in pollen.cli).

Key invariants:
    - Only the packet of this very data directory is ended; another directory's is left alone.
    - A packet that ignores the request is forced; one that honours it goes by itself.
    - Whatever happened to the packet, its leases are released: the lease's scratch is gone.
    - The lease programs a forced packet would orphan are ended; the Warden and what it started
      are left for the CLI, which stops the Warden next.

See Also:
    - packages/pollen/src/pollen/teardown.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import psutil
import pytest
from device_kit import Device

from pollen.agent import PacketStatus, write_status
from pollen.cli import main
from pollen.lease import DeviceLease, LeaseTable
from pollen.platform import CommandResult, WindowsShim, select_shim
from pollen.platform.base import run_command
from pollen.state import WardenRecord, save_state
from pollen.teardown import end_packet, runs_packet
from waggle.clock import Clock
from waggle.ids import new_lease_id
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import RuntimeLevel

ASKED_EXIT = 7  # What the stand-in exits with when it honours the request to stop.
_KILLED_WAIT_S = 2.0  # Long enough for a killed program to go; a live one is only waited this.
_STARTED_WAIT_S = 20.0  # How long the stand-in gets to write its pids.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # A console of its own, as the packet has.
# A stand-in packet, run as a script named pollen.py so its command line has pollen's own shape
# (``pollen.py run --data-dir DIR``, then MODE and PIDS): it starts a lease program and a Warden
# with a child of its own, as the real one does, each in a process group and a windowless console
# of its own; writes the four pids to PIDS, then sleeps; MODE says whether it honours a request to
# stop.
_PACKET = """
import ctypes, json, os, signal, subprocess, sys, time
honours, pids = sys.argv[4] == "honours", sys.argv[5]
def asked(*_):
    open(pids + ".asked", "w").close()
    sys.exit(7)
for name in ("SIGINT", "SIGTERM"):
    signal.signal(getattr(signal, name), asked if honours else signal.SIG_IGN)
if honours and sys.platform == "win32":
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
sleep = [sys.executable, "-c", "import time; time.sleep(60)"]
code = "import subprocess, time; c = subprocess.Popen(%r); print(c.pid, flush=True); time.sleep(60)"
group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
own = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Every program's console is its own.
lease = subprocess.Popen(sleep, creationflags=group | own)
argv = [sys.executable, "-c", code % sleep]
warden = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, creationflags=group | own)
grandchild = int(warden.stdout.readline())
with open(pids + ".tmp", "w") as out:
    json.dump([os.getpid(), lease.pid, warden.pid, grandchild], out)
os.replace(pids + ".tmp", pids)
time.sleep(60)
"""


def _start(
    launcher: Sequence[str], data_dir: Path | str, honours: bool, pids: Path
) -> tuple[subprocess.Popen[bytes], list[int]]:
    """Start the stand-in packet for ``data_dir`` under ``launcher``; return it and the pids."""
    mode = "honours" if honours else "ignores"
    script = pids.with_name("pollen.py")
    script.write_text(_PACKET)
    argv = [*launcher, sys.executable, str(script), "run", "--data-dir", str(data_dir)]
    # SAFETY: this interpreter, the system's console host and fixed arguments.
    launched = subprocess.Popen([*argv, mode, str(pids)], creationflags=_NO_WINDOW)  # noqa: S603
    deadline = time.monotonic() + _STARTED_WAIT_S
    while not pids.exists():
        assert time.monotonic() < deadline, "the stand-in packet never started"
        time.sleep(0.05)
    started: list[int] = json.loads(pids.read_text())
    return launched, started


async def _record(device: Device, clock: Clock, packet: int | None, warden: int | None) -> Path:
    """Write the packet's status (unless None), a Warden record and one open lease.

    Returns:
        The lease's scratch directory.
    """
    layout = device.layout
    if packet is not None:
        status = PacketStatus(clock).snapshot(open_leases=1, outbox_pending=0, silence_s=0.0)
        await write_status(layout.status, status.model_copy(update={"pid": packet}))
    if warden is not None:
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
        (True, False),  # Ignores the request: forced, its lease programs ended.
        (True, True),  # Honours it: it goes by itself, as it would at a terminal.
        (False, False),  # Another data directory's packet: it and its programs are left alone.
    ],
)
async def test_this_directorys_packet_ends_and_its_leases_are_released(
    device: Device, clock: Clock, this_directory: bool, honours: bool
) -> None:
    layout = device.layout
    data_dir = layout.root if this_directory else layout.root.parent / "other"
    launched, pids = _start((), data_dir, honours, layout.root.parent / "pids.json")
    packet, lease_program, warden, warden_child = pids
    try:
        scratch = await _record(device, clock, packet, warden)

        ended = await end_packet(layout, select_shim(), clock, wait_s=5.0 if honours else 1.0)

        assert ended.stuck is None and ended.survivors == () and not ended.is_unreleased
        returncode = await asyncio.to_thread(_wait, launched)
        assert (returncode is not None) is this_directory
        assert (returncode == ASKED_EXIT) is (this_directory and honours)
        assert _ended(lease_program) is this_directory
        assert not scratch.exists()  # This directory's leases are released in every case.
        # The Warden and its own child are the CLI's to stop next.
        assert psutil.Process(warden).is_running() and psutil.Process(warden_child).is_running()
    finally:
        for pid in reversed(pids):
            _kill(pid)
        launched.wait()


async def test_a_packet_with_no_status_yet_and_its_directory_spelled_otherwise_is_found(
    device: Device, clock: Clock
) -> None:
    # Regression: the packet was found only through its status document, by the exact spelling
    # of the data directory; one that had not written its status yet was never stopped.
    layout = device.layout
    spelled = f"{layout.root}{os.sep}."  # The same directory, written another way.
    launched, pids = _start((), spelled, True, layout.root.parent / "pids.json")
    try:
        scratch = await _record(device, clock, None, pids[2])

        ended = await end_packet(layout, select_shim(), clock, wait_s=5.0)

        assert ended.stuck is None and not ended.is_unreleased
        assert await asyncio.to_thread(_wait, launched) == ASKED_EXIT
        assert not scratch.exists()
    finally:
        for pid in reversed(pids):
            _kill(pid)
        launched.wait()


@pytest.mark.parametrize(
    ("argv", "cwd", "matched"),
    [
        # The operator's own wrapper round ``pollen uninstall`` names the directory too.
        (["uv", "run", "pollen", "uninstall", "--data-dir", "{root}"], "{root}", False),
        # A relative --data-dir means the directory below that process's own working directory.
        (["python", "-m", "pollen", "run", "--data-dir", "data"], "{other}", False),
        (["python", "-m", "pollen", "run", "--data-dir", "data"], "{parent}", True),
        (["python", "-m", "pollen", "run", "--data-dir", "data"], None, False),  # cwd unreadable.
        (["C:/bin/pollen.exe", "run", "--data-dir={root}"], None, True),  # The packaged binary.
    ],
)
def test_only_pollens_own_run_for_this_directory_is_the_packet(
    tmp_path: Path, argv: list[str], cwd: str | None, matched: bool
) -> None:
    root = tmp_path / "data"
    places = {"root": str(root), "parent": str(tmp_path), "other": str(tmp_path / "elsewhere")}
    filled = [word.format(**places) for word in argv]

    assert runs_packet(filled, None if cwd is None else cwd.format(**places), root) is matched


@pytest.mark.parametrize("readable", [True, False])
async def test_the_leases_of_a_packet_that_died_long_ago_are_released_or_reported(
    device: Device, clock: Clock, readable: bool
) -> None:
    dead = psutil.Popen([sys.executable, "-c", "pass"])  # A pid that no longer runs.
    dead.wait()
    scratch = await _record(device, clock, dead.pid, warden=None)
    if not readable:
        device.layout.state.unlink()  # Nothing to release them with: the lease stays, and says so.

    ended = await end_packet(device.layout, select_shim(), clock)

    assert ended.stuck is None and ended.is_unreleased is not readable
    assert scratch.exists() is not readable


@pytest.mark.skipif(sys.platform != "win32", reason="the console host exists on Windows only")
async def test_uninstall_stops_a_packet_under_the_headless_console_host_politely(
    device: Device, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: uninstall used to end the task first, which ends the console host and with it
    # the console the CLI presses Ctrl+C on, so the packet was always forced.
    layout = device.layout
    host = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "conhost.exe")
    pid_file = layout.root.parent / "pids.json"
    launched, pids = _start((str(host), "--headless"), layout.root, True, pid_file)
    packet, lease_program, warden, _ = pids

    def schtasks(argv: Sequence[str]) -> CommandResult:
        if argv[0] != "schtasks":
            return run_command(argv)  # taskkill, for the Warden, is the real one.
        if "/End" in argv:  # What the real /End does: it ends the console host.
            for process in psutil.process_iter(["exe"]):
                if process.info["exe"] and Path(process.info["exe"]) == host:
                    with contextlib.suppress(psutil.Error):
                        if packet in {child.pid for child in process.children(recursive=True)}:
                            process.kill()
        return CommandResult(0, "")

    shim = WindowsShim(Path.home(), dict(os.environ), schtasks)
    monkeypatch.setattr("pollen.cli.select_shim", lambda: shim)
    try:
        scratch = await _record(device, clock, packet, warden)

        status = await asyncio.to_thread(main, ["uninstall", "--data-dir", str(layout.root)])

        assert status == 0
        # It went because it was asked (it notes that), not because it was forced.
        assert await asyncio.to_thread(Path(f"{pid_file}.asked").exists)
        assert _ended(lease_program) and _ended(warden)  # The CLI stopped the Warden too.
        assert not scratch.exists()
    finally:
        for pid in reversed(pids):
            _kill(pid)
        launched.kill()
        launched.wait()


def _wait(launched: subprocess.Popen[bytes]) -> int | None:
    """Return the stand-in's exit status if it has ended within a moment, else None."""
    try:
        return launched.wait(_KILLED_WAIT_S)
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
    """End a process a test started, if it still runs."""
    with contextlib.suppress(psutil.NoSuchProcess):
        psutil.Process(pid).kill()
