"""Hold the operating-system shims so the rest of pollen never branches on the host OS.

Linux, macOS and Windows each get a shim behind one ``PlatformShim`` Protocol: where the data
directory lives, how a child process is started in its own process group and signalled as a
group, what the device reports about itself, and how the Pollen Packet (the thin gateway program
on a device the Hive borrows) is installed as a service that starts at login. ``select_shim`` is
the single place that looks at the host and chooses; the Linux and macOS shims share the POSIX
process behaviour in ``posix``.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.cli (the
    composition root) and handed to the executors and the lease code; calls into waggle, psutil
    and the standard library only.

Key invariants:
    - No module outside this package reads ``sys.platform``; they hold a PlatformShim.
    - Service installation removes nothing of the operator's: uninstalling deletes the one unit
      the install wrote.

See Also:
    - docs/waggle/gateway-contract.md section 3 for what a gateway needs from its platform.
    - .claude/codingrules.md section 2 for the hosts the Hive supports.

Public API:
    - PlatformShim: the Protocol every operating-system shim satisfies.
    - PlatformFacts, ProcessRef, SpawnSpec, ServicePlan, ServiceResult, CommandResult: its values.
    - select_shim: choose the shim for the host.
    - launch_command: the argv that starts ``pollen run`` the way this process started.
    - EXIT_NO_RESTART: the exit status that tells a service manager not to restart the packet.
    - LinuxShim, MacOsShim, WindowsShim: the three implementations.
"""

from pollen.platform.base import (
    EXIT_NO_RESTART,
    CommandResult,
    PlatformFacts,
    PlatformShim,
    ProcessRef,
    ServicePlan,
    ServiceResult,
    SpawnSpec,
    launch_command,
)
from pollen.platform.linux import LinuxShim
from pollen.platform.macos import MacOsShim
from pollen.platform.select import select_shim
from pollen.platform.windows import WindowsShim

__all__ = [
    "EXIT_NO_RESTART",
    "CommandResult",
    "LinuxShim",
    "MacOsShim",
    "PlatformFacts",
    "PlatformShim",
    "ProcessRef",
    "ServicePlan",
    "ServiceResult",
    "SpawnSpec",
    "WindowsShim",
    "launch_command",
    "select_shim",
]
