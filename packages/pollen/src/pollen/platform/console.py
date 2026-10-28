"""Reach a Windows program through its console: the break event and Ctrl+C, from the right console.

On Windows the Pollen Packet (the thin gateway program on a device the Hive borrows) runs, as the
installed task, in a console with no window (``conhost.exe --headless``). Every program it starts,
the on-device Warden included, gets a windowless console of its own, so nothing appears on the
operator's desktop and nothing a program does to its console (it may well press Ctrl+C or
Ctrl+Break there for its own children) can reach the packet; the Warden starts its own commands
the same way (hivemind.cell.local). A console control event (Ctrl+Break, Ctrl+C: the polite
"please stop") reaches only processes attached to the console it is raised on, and for any other
the call reports success and nothing arrives. So a program on another console is asked by a
short-lived helper: the packet runs itself with the hidden ``console-signal`` verb, in a
windowless console of its own, and the helper attaches to the target's console, raises the event
there and exits (``signal_console``). It is given the target's creation time and refuses a pid
that now belongs to another process. Ctrl+Break goes to the target's process group alone; Ctrl+C
reaches the whole console, so the helper presses it only on a console that holds nothing but the
target's own family (``pollen uninstall`` uses it to stop the packet, never on a terminal the
operator also uses). The packet turns Ctrl+C back on for itself when it starts (a program
inherits "ignore Ctrl+C" from whoever started it) and ignores Ctrl+Break, which is not its stop
signal. The calls are plain kernel32 through ctypes, read only on Windows, so the module imports
(and its callers are tested) on every host.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Used by
    pollen.platform.windows and by pollen.cli's hidden helper verb; calls into ctypes, psutil,
    signal and os only.

Key invariants:
    - No program the packet starts shares its console, the Warden included.
    - ``signal_console`` runs in a short-lived helper process, never in the packet, and only on
      the console of the process whose pid and creation time it was given.
    - Ctrl+C is pressed only on a console that holds nothing but the target, its own ancestors and
      descendants, and the helper itself.
    - Off Windows nothing here touches a console.

See Also:
    - pollen.platform.windows, the shim that runs the helper.
    - pollen.teardown, which asks the packet to stop through ``ask_to_stop``.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import sys

import psutil

CONSOLE_PIDS_MAX = 1024  # Room for every process on one console: a program and its children.
CREATE_TIME_TOLERANCE_S = 1.0  # As the shims': psutil's creation time is stable per process.
BREAK = "break"  # The helper's event names, as its command line gives them.
CTRL_C = "ctrl-c"
_EVENTS = {CTRL_C: 0, BREAK: 1}  # GenerateConsoleCtrlEvent's CTRL_C_EVENT and CTRL_BREAK_EVENT.

__all__ = [
    "BREAK",
    "CONSOLE_PIDS_MAX",
    "CREATE_TIME_TOLERANCE_S",
    "CTRL_C",
    "accept_interrupts",
    "signal_console",
]


def signal_console(pid: int, created: float, event: str) -> bool:
    """Raise ``event`` on the console ``pid`` is attached to; the helper verb's whole job.

    Args:
        pid: The program to ask: a lease program or the Warden (Ctrl+Break, to its process group
            alone) or the packet (Ctrl+C, to its whole console).
        created: The creation time ``pid`` was recorded with; another process now holding the pid
            is refused.
        event: ``BREAK`` or ``CTRL_C``.

    Returns:
        False when ``pid`` is not that process any more, has no console to attach to, or (for
        Ctrl+C) shares it with anything but its own family and this helper, so nothing was raised.
    """
    if not _is_recorded(pid, created):
        return False
    if sys.platform != "win32":
        return False
    else:
        kernel32 = ctypes.windll.kernel32
        # SAFETY: documented console calls in a helper process that exits right after; it
        # ignores Ctrl+C itself, so the event it raises cannot end it before it reports.
        kernel32.FreeConsole()  # Leaves its own windowless console for the target's.
        if not kernel32.AttachConsole(pid):
            return False
        kernel32.SetConsoleCtrlHandler(None, True)
        if event == CTRL_C:
            present = set(_console_pids())
            # An empty list means it could not be read: as foreign as an operator's terminal.
            if not present or not present <= _family(pid):
                return False
            return bool(kernel32.GenerateConsoleCtrlEvent(_EVENTS[CTRL_C], 0))
        return bool(kernel32.GenerateConsoleCtrlEvent(_EVENTS[BREAK], pid))


def accept_interrupts() -> None:
    """Hear Ctrl+C (the packet's stop) whatever was inherited, and ignore Ctrl+Break."""
    if sys.platform == "win32":
        # SAFETY: clears this process's inherited "ignore Ctrl+C" flag; handlers are unchanged.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
        # Ctrl+Break is never the packet's stop signal, wherever it was raised.
        signal.signal(signal.SIGBREAK, signal.SIG_IGN)


def _is_recorded(pid: int, created: float) -> bool:
    """Say whether ``pid`` still belongs to the process created at ``created``."""
    try:
        return abs(psutil.Process(pid).create_time() - created) <= CREATE_TIME_TOLERANCE_S
    except psutil.Error:
        return False  # Gone, or not readable: not one the helper can vouch for.


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
