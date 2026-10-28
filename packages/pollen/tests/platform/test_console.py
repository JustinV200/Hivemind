"""Test the Windows console moves: the helper's Ctrl+Break and Ctrl+C, and what never shares.

On Windows the Pollen Packet (the thin gateway program on a device the Hive borrows) gives every
program it starts, the Warden included, a windowless console of its own, so nothing a program (or
a command the Warden runs) raises on its console reaches the packet, and asks a program to stop
through a short-lived helper that raises Ctrl+Break on that console, given the program's pid and
creation time (pollen.platform.console). ``pollen uninstall`` asks the packet the same way with
Ctrl+C, but never on a console it shares with anything outside its own family. These tests run
real programs: a lease program with a break handler, asked with the right and wrong creation
times; a packet stand-in under ``conhost.exe --headless`` (as the installed task runs it) with a
Warden, where a lease program or a command of the Warden's broadcasts both events; and a packet
stand-in that shares its console with a stranger.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.platform.console through
    pollen.platform.windows.

Key invariants:
    - An INTERRUPT reaches a lease program in its own console gracefully, through the helper;
      the helper refuses a pid whose creation time does not match.
    - A broadcast Ctrl+C or Ctrl+Break never reaches the packet, whether a lease program or a
      command of the Warden's raises it, and a lease program's never reaches the Warden.
    - Ctrl+C is not pressed on a console a process outside the packet's family shares.

See Also:
    - packages/pollen/src/pollen/platform/console.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from pollen.platform import CONSOLE_SIGNAL_VERB, SpawnSpec, WindowsShim
from pollen.platform.base import own_command, run_command
from waggle.messages.session import ProcessSignal

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="consoles are a Windows matter")

ASKED_EXIT = 3  # What a stand-in exits with when it was asked to stop and did.
_STARTED_WAIT_S = 20.0  # How long a stand-in gets to write what it reports.
_HOST = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "conhost.exe")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# A lease program that stops cleanly on Ctrl+Break; short sleeps, so the handler runs at once.
_BREAKABLE = """
import signal, sys, time
def asked(*_):
    sys.exit(3)
signal.signal(signal.SIGBREAK, asked)
print("ready", flush=True)
for _ in range(600):
    time.sleep(0.1)
"""
# A packet stand-in, run as the task runs the packet: it hears Ctrl+C (noting it), starts a Warden
# through the real shim, and a broadcaster that raises Ctrl+C and Ctrl+Break on its console: a
# lease program the packet starts, or (argv[2] == "warden") a command the Warden starts on its own
# console, the worst a Warden's command can do. Then it reports whether either event reached it
# or the Warden.
_BROADCAST = """
import asyncio, json, signal, subprocess, sys
from pathlib import Path
from pollen.platform import SpawnSpec, WindowsShim
from pollen.platform.console import accept_interrupts
from waggle.messages.session import ProcessSignal
heard = []
signal.signal(signal.SIGINT, lambda *_: heard.append("ctrl-c"))
accept_interrupts()
LOUD = (
    "import ctypes, time\\n"
    "k = ctypes.windll.kernel32\\n"
    "k.SetConsoleCtrlHandler(None, True)\\n"
    "k.GenerateConsoleCtrlEvent(0, 0)\\n"
    "k.GenerateConsoleCtrlEvent(1, 0)\\n"
    "time.sleep(0.5)\\n"
)
FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP  # Inherits the Warden's console.
WARDEN = (
    "import subprocess, sys, time\\n"
    "if sys.argv[2] == 'warden':\\n"
    f"    subprocess.Popen([sys.executable, '-c', sys.argv[1]], creationflags={FLAGS})\\n"
    "for _ in range(600):\\n"
    "    time.sleep(0.1)\\n"
)
async def main():
    shim = WindowsShim(Path.home(), {})
    def spec(*argv):
        return SpawnSpec((sys.executable, "-c", *argv), Path.cwd(), {}, None, None, None)
    warden = await shim.spawn(spec(WARDEN, LOUD, sys.argv[2]))
    if sys.argv[2] == "lease":
        await (await shim.spawn(spec(LOUD))).wait()
    for _ in range(30):
        await asyncio.sleep(0.1)
    report = {"heard": heard, "warden_alive": warden.returncode is None}
    shim.signal_group(shim.process_ref(warden.pid), ProcessSignal.KILL)
    await warden.wait()
    Path(sys.argv[1]).write_text(json.dumps(report))
asyncio.run(main())
"""
# A launcher under one console: a packet stand-in that notes Ctrl+C, and a stranger beside it.
_SHARED = """
import json, os, subprocess, sys, time
packet_code = (
    "import signal, sys, time\\n"
    "def asked(*_):\\n"
    "    open(sys.argv[1], 'w').close()\\n"
    "    sys.exit(3)\\n"
    "signal.signal(signal.SIGINT, asked)\\n"
    "import ctypes; ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)\\n"
    "for _ in range(600):\\n"
    "    time.sleep(0.1)\\n"
)
packet = subprocess.Popen([sys.executable, "-c", packet_code, sys.argv[1] + ".asked"])
stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(sys.argv[1] + ".tmp", "w") as out:
    json.dump([packet.pid, stranger.pid], out)
os.replace(sys.argv[1] + ".tmp", sys.argv[1])
time.sleep(60)
"""


async def test_an_interrupt_reaches_a_lease_program_in_its_own_console_gracefully(
    tmp_path: Path,
) -> None:
    shim = WindowsShim(tmp_path, {})  # The real helper.
    spec = SpawnSpec(
        (sys.executable, "-c", _BREAKABLE),
        tmp_path,
        {},
        subprocess.DEVNULL,
        subprocess.PIPE,
        subprocess.DEVNULL,
    )
    process = await shim.spawn(spec)
    assert process.stdout is not None
    await asyncio.wait_for(process.stdout.readline(), timeout=20)
    ref = shim.process_ref(process.pid)
    try:
        assert shim.signal_group(ref, ProcessSignal.INTERRUPT) is True
        await asyncio.wait_for(process.communicate(), timeout=20)
        assert process.returncode == ASKED_EXIT  # Its handler ran: asked, not forced.
    finally:
        shim.signal_group(ref, ProcessSignal.KILL)


@pytest.mark.parametrize("broadcaster", ["lease", "warden"])
def test_a_broadcast_reaches_neither_the_packet_nor_the_warden(
    tmp_path: Path, broadcaster: str
) -> None:
    # Regression: lease programs, then the Warden and the commands it ran, once shared the
    # packet's console, so a Ctrl+C or Ctrl+Break one of them raised stopped the packet and the
    # Warden.
    report = tmp_path / "report.json"
    argv = [str(_HOST), "--headless", sys.executable, "-c", _BROADCAST, str(report), broadcaster]
    # SAFETY: the system's console host, this interpreter and fixed arguments.
    host = subprocess.Popen(argv, creationflags=_NO_WINDOW)  # noqa: S603
    try:
        found = json.loads(_await_file(report))
        assert found["heard"] == []
        # A command on the Warden's own console reaches the Warden (hivemind.cell.local gives
        # its commands consoles of their own for that); a lease program never does.
        assert found["warden_alive"] or broadcaster == "warden"
    finally:
        _kill_tree(host.pid)
        host.wait()


def test_ctrl_c_is_not_pressed_on_a_console_a_stranger_shares(tmp_path: Path) -> None:
    pids_file = tmp_path / "pids.json"
    argv = [str(_HOST), "--headless", sys.executable, "-c", _SHARED, str(pids_file)]
    # SAFETY: the system's console host, this interpreter and fixed arguments.
    host = subprocess.Popen(argv, creationflags=_NO_WINDOW)  # noqa: S603
    try:
        packet, _stranger = json.loads(_await_file(pids_file))

        refused = not WindowsShim(tmp_path, {}).ask_to_stop(packet)

        time.sleep(1.0)  # Time for an event, had one been raised, to be handled.
        assert refused
        assert not Path(f"{pids_file}.asked").exists()
        assert psutil.Process(packet).is_running()
    finally:
        _kill_tree(host.pid)
        host.wait()


@pytest.mark.parametrize(("offset", "delivered"), [(0.0, True), (-1000.0, False), (5.0, False)])
async def test_the_helper_refuses_a_pid_whose_creation_time_does_not_match(
    tmp_path: Path, offset: float, delivered: bool
) -> None:
    shim = WindowsShim(tmp_path, {})
    spec = SpawnSpec(
        (sys.executable, "-c", _BREAKABLE),
        tmp_path,
        {},
        subprocess.DEVNULL,
        subprocess.PIPE,
        subprocess.DEVNULL,
    )
    process = await shim.spawn(spec)
    assert process.stdout is not None
    await asyncio.wait_for(process.stdout.readline(), timeout=20)
    ref = shim.process_ref(process.pid)
    try:
        created = repr(ref.created + offset)
        helper = [*own_command(), CONSOLE_SIGNAL_VERB, "break", str(ref.pid), created]
        ran = await asyncio.to_thread(run_command, helper)

        assert (ran.returncode == 0) is delivered
        if delivered:
            await asyncio.wait_for(process.wait(), timeout=20)
            assert process.returncode == ASKED_EXIT
        else:
            await asyncio.sleep(1.0)  # Time for an event, had one been raised, to arrive.
            assert process.returncode is None
    finally:
        shim.signal_group(ref, ProcessSignal.KILL)
        await asyncio.wait_for(process.communicate(), timeout=20)


def _await_file(path: Path) -> str:
    """Wait for a stand-in to write ``path``; return what it wrote."""
    deadline = time.monotonic() + _STARTED_WAIT_S
    while not path.exists():
        assert time.monotonic() < deadline, f"the stand-in never wrote {path.name}"
        time.sleep(0.05)
    return path.read_text()


def _kill_tree(pid: int) -> None:
    """End a stand-in's console host and everything below it."""
    with contextlib.suppress(psutil.NoSuchProcess):
        root = psutil.Process(pid)
        for process in [*root.children(recursive=True), root]:
            with contextlib.suppress(psutil.NoSuchProcess):
                process.kill()
