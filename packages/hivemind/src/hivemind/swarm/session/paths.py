"""Provide DevicePaths: reason about a device's file paths without touching the Hive Stand's disk.

A `PollenSession` runs commands and moves files on another machine, so every path in a request is
a path on that machine, spelled the way its operating system spells paths. `Path.resolve()` on the
Hive Stand would be wrong twice over: it would follow the Hive Stand's own symbolic links, and it
would resolve a relative path against the Hive Stand's working directory. `DevicePaths` therefore
works on the text of a path with the standard library's `posixpath` or `ntpath` (both are pure
string functions, available on every platform), choosing the one the lease's scratch root is
written in. It joins a relative path under the scratch root, collapses `.` and `..` segments, and
says whether the result lies inside scratch or under a path the lease allows. It cannot see the
device's symbolic links, and does not pretend to: the Pollen Packet resolves them on the device and
checks again (gateway contract section 12), so this module is the Hive's first wall, not the only
one. A lease's allowed paths are read live on every question, because a lease can be widened after
its session exists.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Built once per session by `PollenSession`; used by its exec, file and process operations to
    resolve a path, refuse one out of reach and name the capability a path needs. Calls into
    hivemind.cell only.

Key invariants:
    - No function here touches a filesystem, so nothing depends on the Hive Stand's own layout.
    - A resolved path is absolute and has no `.` or `..` segment; two spellings of one path
      resolve to one string.
    - "Inside scratch" and "reachable" are lexical: a path reached only through a link on the
      device is judged by the device.
    - Windows paths compare case-insensitively, as the device's file system does.

See Also:
    - docs/waggle/gateway-contract.md section 12 for the device's own resolution rules.
    - hivemind.cell.session.resolve_scratch_path for the local equivalent this mirrors.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from pathlib import Path
from types import ModuleType

from hivemind.cell.errors import PathNotAllowedError
from hivemind.cell.lease import RealCellLease

# A scratch root that starts with a drive letter or a UNC prefix is a Windows path; anything else
# is read as POSIX. The root comes from the device's own `cell.lease_opened`, so it spells paths
# the way the device does.
_WINDOWS_ROOT = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

__all__ = ["DevicePaths"]


class DevicePaths:
    """Resolve and judge the paths of one lease on one device, by their text alone."""

    def __init__(self, lease: RealCellLease) -> None:
        """Build the path rules for `lease`'s scratch root.

        Args:
            lease: The lease whose scratch root and (live) allowed paths bound every path.
        """
        self._lease = lease
        root = str(lease.scratch_root)
        self._flavor: ModuleType = ntpath if _WINDOWS_ROOT.match(root) else posixpath
        self._scratch = str(self._flavor.normpath(root))

    @property
    def scratch_root(self) -> str:
        """The lease's scratch root as normalised device text."""
        return self._scratch

    def resolve(self, path: Path | str) -> str:
        """Return the absolute, normalised device path `path` names.

        Args:
            path: A device path, absolute or relative to the scratch root.

        Returns:
            The path with a relative one joined under scratch and `.` and `..` collapsed.
        """
        text = str(path)
        joined = text if self._flavor.isabs(text) else self._flavor.join(self._scratch, text)
        return str(self._flavor.normpath(joined))

    def is_inside_scratch(self, resolved: str) -> bool:
        """Whether a resolved path is the scratch root or lies beneath it."""
        return self._within(self._scratch, resolved)

    def is_reachable(self, resolved: str) -> bool:
        """Whether a resolved path is inside scratch or under a path the lease allows right now."""
        if self.is_inside_scratch(resolved):
            return True
        # Read from the lease on every call: a lease widened after the session was built (a kept
        # root, a declared Leaving) must be honoured at once.
        return any(
            self._within(self._flavor.normpath(str(allowed)), resolved)
            for allowed in self._lease.allowed_paths
        )

    def require_reach(self, resolved: str) -> None:
        """Raise `PathNotAllowedError` unless a resolved path is inside scratch or allowed."""
        if not self.is_reachable(resolved):
            raise PathNotAllowedError(Path(resolved))

    def require_inside_scratch(self, resolved: str) -> None:
        """Raise `PathNotAllowedError` unless a resolved path is inside scratch itself."""
        if not self.is_inside_scratch(resolved):
            raise PathNotAllowedError(Path(resolved))

    def basename(self, text: str) -> str:
        """Return the last segment of a device path, in the device's own spelling."""
        return str(self._flavor.basename(text))

    def _within(self, root: str, path: str) -> bool:
        """Whether `path` equals `root` or lies beneath it, comparing in the device's case rules."""
        normal_root = self._flavor.normcase(root)
        normal_path = self._flavor.normcase(path)
        try:
            return bool(self._flavor.commonpath([normal_root, normal_path]) == normal_root)
        except ValueError:
            # Different drives, or an absolute path against a relative one: nothing in common.
            return False
