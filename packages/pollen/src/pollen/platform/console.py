"""Reach a Windows program through its console: the break event, and Ctrl+C from another process.

On Windows the Pollen Packet (the thin gateway program on a device the Hive borrows) runs with a
console that has no window, and so does every program it starts for a lease, each in a console of
its own. A console control event (Ctrl+C, Ctrl+Break) is the polite "please stop" there, but it
only reaches processes attached to the sender's console, and for any other the call reports
success and nothing arrives. This module holds the three console moves the Windows shim needs:
telling whether a process shares this one's console, sending the break event to one that does,
and, for ``pollen uninstall`` (a separate process), visiting the packet's console to press Ctrl+C
there so the packet stops the way it would at a terminal, releasing its leases on the way out. A
program inherits "ignore Ctrl+C" from whoever started it, so the packet turns Ctrl+C back on for
itself when it starts. The calls are plain kernel32 through ctypes, read only on Windows, so the
module imports (and its callers are tested) on every host.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.platform.windows only; calls into ctypes, signal and os only.

Key invariants:
    - ``interrupt_console`` is for a short-lived command-line process only: it leaves its own
      console for a moment, which the packet itself must never do (its console is the one its
      Warden shares, and is gone once nobody is attached).
    - The visiting process ignores Ctrl+C from then on, so the event it raised can never reach it.
    - Off Windows nothing here touches a console: the break stand-in is SIGINT to a pid.

See Also:
    - pollen.platform.windows, the shim that calls these.
    - pollen.teardown, which asks the packet to stop through ``ask_to_stop``.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys

CONSOLE_PIDS_MAX = 1024  # Room for every process on the packet's console: itself, a Warden, a few.
_CTRL_C_EVENT = 0  # GenerateConsoleCtrlEvent's Ctrl+C.
_ATTACH_PARENT_PROCESS = 0xFFFFFFFF  # AttachConsole's "the console of the process that started me".
# Read by name so the module imports on every host; the fallback is never used on Windows.
_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)

__all__ = ["CONSOLE_PIDS_MAX", "accept_interrupts", "interrupt_console", "send_break"]


def send_break(pid: int) -> bool:
    """Send the break event to the process group ``pid`` leads; False if it cannot reach it.

    Every program the shim spawns leads a process group of its own, so the event reaches that
    group alone. It reaches only a process attached to this process's console (the Warden): for
    any other the call reports success and nothing arrives, so it is not made.
    """
    if not on_our_console(pid):
        return False
    try:
        os.kill(pid, _BREAK_EVENT)
    except OSError:
        return False
    return True


def on_our_console(pid: int) -> bool:
    """Say whether ``pid`` is attached to this process's console; False when there is none."""
    # The else is load-bearing for mypy's Linux run, as in pollen.platform.posix.
    if sys.platform != "win32":
        return True  # The stand-in event off Windows is SIGINT, which reaches any pid.
    else:
        pids = (ctypes.c_ulong * CONSOLE_PIDS_MAX)()
        # SAFETY: a read-only query into a buffer sized by ctypes; it fills nothing when too small.
        count = ctypes.windll.kernel32.GetConsoleProcessList(pids, CONSOLE_PIDS_MAX)
        return 0 < count <= CONSOLE_PIDS_MAX and pid in pids[:count]


def interrupt_console(pid: int) -> bool:
    """Press Ctrl+C on the console ``pid`` is attached to, from outside it; False if it has none.

    Every process on that console gets the event; the packet stops as at a terminal, and a
    program in a process group of its own (the Warden) ignores it. This process leaves its own
    console for the moment it takes and then rejoins its parent's.
    """
    if sys.platform != "win32":
        return False  # A POSIX packet is asked with SIGTERM instead.
    else:
        kernel32 = ctypes.windll.kernel32
        # SAFETY: documented console calls; this process ignores Ctrl+C before raising it, and is
        # put back on its parent's console on every path.
        kernel32.FreeConsole()
        try:
            if not kernel32.AttachConsole(pid):
                return False
            kernel32.SetConsoleCtrlHandler(None, True)
            return bool(kernel32.GenerateConsoleCtrlEvent(_CTRL_C_EVENT, 0))
        finally:
            kernel32.FreeConsole()
            kernel32.AttachConsole(_ATTACH_PARENT_PROCESS)


def accept_interrupts() -> None:
    """Let Ctrl+C reach this process, whatever it inherited from the program that started it."""
    if sys.platform == "win32":
        # SAFETY: clears this process's inherited "ignore Ctrl+C" flag; handlers are unchanged.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
