"""Reach a Windows program through its console: the break event, and Ctrl+C from another process.

On Windows the Pollen Packet (the thin gateway program on a device the Hive borrows) runs, as the
installed task, in a console with no window (``conhost.exe --headless``). The programs it starts
share that console in process groups of their own, so no window appears and a break event (the
polite "please stop") still reaches each of them. When the packet has a console with a window
(``pollen run`` in a terminal) or none, a program it starts gets a windowless console of its own
instead, and only the Warden, which must be asked politely, shares a visible one. A console
control event only reaches processes attached to the sender's console, and for any other the call
reports success and nothing arrives. This module holds the console moves the Windows shim needs:
what kind of console this process has, whether a process shares it, sending the break event to
one that does, and, for ``pollen uninstall`` (a separate process), visiting the packet's console
to press Ctrl+C there so the packet stops the way it would at a terminal, releasing its leases on
the way out. A program inherits "ignore Ctrl+C" from whoever started it, so the packet turns
Ctrl+C back on for itself when it starts. The calls are plain kernel32 through ctypes, read only
on Windows, so the module imports (and its callers are tested) on every host.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.platform.windows only; calls into ctypes, psutil, signal and os only.

Key invariants:
    - ``interrupt_console`` is for a short-lived command-line process only: it leaves its own
      console for a moment, which the packet itself must never do (its console is the one its
      programs share, and is gone once nobody is attached).
    - Ctrl+C is pressed only on a console that holds nothing but the packet, its own ancestors and
      descendants, and the visiting process: never on a terminal the operator also uses.
    - The visiting process ignores Ctrl+C while it raises the event and until that event has
      reached it, so it never ends the visitor; then it hears Ctrl+C again.
    - Off Windows nothing here touches a console: the break stand-in is SIGINT to a pid.

See Also:
    - pollen.platform.windows, the shim that calls these.
    - pollen.teardown, which asks the packet to stop through ``ask_to_stop``.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import sys
import time

import psutil

CONSOLE_PIDS_MAX = 1024  # Room for every process on the packet's console: itself and its programs.
_CTRL_C_EVENT = 0  # GenerateConsoleCtrlEvent's Ctrl+C.
CTRL_DELIVERY_S = 0.5  # How long the raised Ctrl+C gets to arrive, ignored, at the raiser.
_ATTACH_PARENT_PROCESS = 0xFFFFFFFF  # AttachConsole's "the console of the process that started me".
# Read by name so the module imports on every host; the fallback is never used on Windows.
_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)

__all__ = [
    "CONSOLE_PIDS_MAX",
    "CTRL_DELIVERY_S",
    "accept_interrupts",
    "has_console",
    "interrupt_console",
    "is_headless",
    "send_break",
]


def send_break(pid: int) -> bool:
    """Send the break event to the process group ``pid`` leads; False if it cannot reach it.

    Every program the shim spawns leads a process group of its own, so the event reaches that
    group alone. It reaches only a process attached to this process's console: for any other the
    call reports success and nothing arrives, so it is not made.
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
        return pid in _console_pids()


def has_console() -> bool:
    """Say whether this process is attached to a console at all (always False off Windows)."""
    return sys.platform == "win32" and bool(_console_pids())


def is_headless() -> bool:
    """Say whether this process's console has no window: the installed task's ``--headless``."""
    if sys.platform != "win32":
        return False
    else:
        return has_console() and not ctypes.windll.kernel32.GetConsoleWindow()


def interrupt_console(pid: int) -> bool:
    """Press Ctrl+C on the console ``pid`` is attached to, from outside it.

    Every process on that console gets the event; the packet stops as at a terminal, and the
    programs it started, each in a process group of its own, ignore it. This process leaves its
    own console for the moment it takes and then rejoins its parent's.

    Returns:
        False when ``pid`` has no console, or shares it with anything other than its own family
        and this process (an operator's terminal), so nothing was pressed.
    """
    if sys.platform != "win32":
        return False  # A POSIX packet is asked with SIGTERM instead.
    else:
        kernel32 = ctypes.windll.kernel32
        # SAFETY: documented console calls; this process ignores Ctrl+C while it raises it, and is
        # put back on its parent's console, hearing Ctrl+C again, on every path.
        kernel32.FreeConsole()
        try:
            attached = kernel32.AttachConsole(pid)
            present = set(_console_pids()) if attached else set()
            # An empty list means it could not be read: as foreign as an operator's terminal.
            if not present or not present <= _family(pid):
                return False
            kernel32.SetConsoleCtrlHandler(None, True)
            pressed = bool(kernel32.GenerateConsoleCtrlEvent(_CTRL_C_EVENT, 0))
            # The event reaches this process too, a moment later: hearing Ctrl+C again before it
            # has arrived would end this process (measured: at once it does, after 0.1 s not).
            time.sleep(CTRL_DELIVERY_S)
            return pressed
        finally:
            kernel32.FreeConsole()
            kernel32.AttachConsole(_ATTACH_PARENT_PROCESS)
            kernel32.SetConsoleCtrlHandler(None, False)


def accept_interrupts() -> None:
    """Let Ctrl+C reach this process, whatever it inherited from the program that started it."""
    if sys.platform == "win32":
        # SAFETY: clears this process's inherited "ignore Ctrl+C" flag; handlers are unchanged.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)


def _family(pid: int) -> set[int]:
    """Return ``pid``, its ancestors and descendants, and this process: who may share it."""
    family = {pid, os.getpid()}
    with contextlib.suppress(psutil.Error):  # A process gone mid-look leaves the set smaller.
        process = psutil.Process(pid)
        family.update(parent.pid for parent in process.parents())
        family.update(child.pid for child in process.children(recursive=True))
    return family


def _console_pids() -> list[int]:
    """List the processes attached to this process's console; empty when it has none."""
    if sys.platform != "win32":
        return []
    else:
        pids = (ctypes.c_ulong * CONSOLE_PIDS_MAX)()
        # SAFETY: a read-only query into a buffer sized by ctypes; it fills nothing when too small.
        count = ctypes.windll.kernel32.GetConsoleProcessList(pids, CONSOLE_PIDS_MAX)
        return list(pids[:count]) if 0 < count <= CONSOLE_PIDS_MAX else []
