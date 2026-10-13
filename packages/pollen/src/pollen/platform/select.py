"""Choose the PlatformShim for the host: the one place pollen branches on the operating system.

The Pollen Packet (the thin gateway program on a device the Hive borrows) has exactly one
operating-system branch, and it is here: ``select_shim`` looks at the platform name and returns
the Windows, macOS or Linux shim, each behind the same ``PlatformShim`` Protocol, so every other
module stays operating-system neutral and is tested with whichever shim (or fake) a test hands
it. Any POSIX system that is neither macOS nor Windows is treated as Linux: it has XDG paths and
process groups, and its service manager, if any, is found or not found by the install command.
The home directory and environment are parameters so a test (or the composition root) decides
what the shim sees; nothing is read at import time.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.cli, the
    composition root, once per command; calls into the three shims and the standard library only.

Key invariants:
    - The only reader of ``sys.platform`` in pollen's own code is this module.
    - The returned shim satisfies PlatformShim; callers never learn which class they hold.

See Also:
    - pollen.platform.base for the Protocol.
    - docs/waggle/gateway-contract.md section 3 for what a gateway needs from its platform.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from pollen.platform.base import CommandRunner, PlatformShim, run_command
from pollen.platform.linux import LinuxShim
from pollen.platform.macos import MacOsShim
from pollen.platform.windows import WindowsShim

__all__ = ["select_shim"]


def select_shim(
    platform_name: str | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    runner: CommandRunner = run_command,
) -> PlatformShim:
    """Return the shim for ``platform_name`` (the running host by default).

    Args:
        platform_name: A ``sys.platform`` value; the running host's when None.
        home: The account's home directory; ``Path.home()`` when None.
        env: The environment the shim reads facts from; the process environment when None.
        runner: Runs service-manager commands; the real one by default.

    Returns:
        A WindowsShim for ``win32``, a MacOsShim for ``darwin``, a LinuxShim for anything else.
    """
    name = sys.platform if platform_name is None else platform_name
    home_dir = Path.home() if home is None else home
    environment = dict(os.environ) if env is None else env
    # The one branch on the operating system in the package.
    if name == "win32":
        return WindowsShim(home_dir, environment, runner)
    if name == "darwin":
        return MacOsShim(home_dir, environment, runner)
    return LinuxShim(home_dir, environment, runner)
