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

The same text rules are the lease's own `PathRule` (`DevicePathRule`): a `RealCellLease` judges the
paths of the machine it leases through the rule its source gave it, and a lease on a device must
never ask the Hive Stand's filesystem about them. `DevicePaths` is built on that rule, so the
session and the lease agree on what a path is. `absolute_device_path` is the same text rules
applied to a path the operator names for the device with no lease in play, such as a directory its
Warden is to watch. A lease holds the device's paths as `Path` objects, whose separators are the
Hive Stand's (backslashes on Windows), so every one is read back through `device_text`, which
gives the device's own spelling, and never through `str()`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.session`.
    Built once per session by `PollenSession`; used by its exec, file and process operations to
    resolve a path, refuse one out of reach and name the capability a path needs. Calls into
    hivemind.cell only.

Key invariants:
    - No function here touches a filesystem, so nothing depends on the Hive Stand's own layout.
    - The lease's rule and the session's paths are one set of rules: the same text resolves to
      the same string through either.
    - A resolved path is absolute and has no `.` or `..` segment; two spellings of one path
      resolve to one string.
    - "Inside scratch" and "reachable" are lexical: a path reached only through a link on the
      device is judged by the device.
    - Windows paths compare case-insensitively, as the device's file system does.
    - A `Path` reaches the text rules only through `device_text`, so a Windows Hive Stand judges a
      POSIX device's path as the device spells it.

See Also:
    - docs/waggle/gateway-contract.md section 12 for the device's own resolution rules.
    - hivemind.cell.session.resolve_scratch_path for the local equivalent this mirrors.
    - hivemind.cell.lease.PathRule for the rule a lease asks, which `DevicePathRule` answers.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from pathlib import Path, PurePath, PureWindowsPath
from types import ModuleType

from hivemind.cell.errors import PathNotAllowedError
from hivemind.cell.lease import RealCellLease

# A scratch root that starts with a drive letter or a UNC prefix is a Windows path; anything else
# is read as POSIX. The root comes from the device's own `cell.lease_opened`, so it spells paths
# the way the device does.
_WINDOWS_ROOT = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

__all__ = ["DevicePathRule", "DevicePaths", "absolute_device_path", "device_text"]


def absolute_device_path(text: str) -> str | None:
    """Return `text` as an absolute, normalised device path, or None when it is not one.

    The device's own spelling decides what "absolute" means, so the answer never depends on the
    operating system the Hive Stand runs on: `/var/log` and a drive letter with a folder are
    absolute everywhere, `logs` and a drive letter with a bare name nowhere. It is how a path the
    operator types for another machine (a directory to watch) is checked before it is stored, with
    no read of any file system.

    Args:
        text: A path as the operator wrote it for the device.

    Returns:
        The path with its separators and `.` and `..` segments in the device's normal form; None
        when it is empty, holds a NUL byte, or is relative.
    """
    if not text or "\0" in text:
        return None
    # The same flavour choice a lease's scratch root makes: a drive letter or a UNC prefix is a
    # Windows path, anything else is read as POSIX.
    flavor: ModuleType = ntpath if _WINDOWS_ROOT.match(text) else posixpath
    if not flavor.isabs(text):
        return None
    return str(flavor.normpath(text))


def device_text(path: PurePath | str) -> str:
    """Return a device path the Hive holds as a `Path` in the device's own spelling.

    A lease holds a device's scratch root and allowed paths as `Path` objects, and a `Path` is the
    Hive Stand's: on Windows it spells a POSIX device's `/pollen/scratch` with backslashes, which
    `posixpath` reads as one relative name. A Windows `Path` with no drive and no UNC share is
    therefore a POSIX device's path and is given back with forward slashes. One with either is a
    Windows device's path, and keeps its spelling, as every path does on a POSIX Hive Stand, whose
    `Path` never rewrites a separator. Text is already the device's, and is returned as it is.

    Args:
        path: A device path as text, or as a `Path` built from the device's text.

    Returns:
        The path's text as the device spells it; not normalised.
    """
    if isinstance(path, str):
        return path
    if isinstance(path, PureWindowsPath) and not path.drive:
        return path.as_posix()
    return str(path)


class DevicePathRule:
    """A device's text rules for paths: the `PathRule` of a lease on it."""

    def __init__(self, scratch_root: str) -> None:
        """Choose the device's path flavour from the way its scratch root is spelled.

        Args:
            scratch_root: The lease's scratch root as the device reported it.
        """
        self._flavor: ModuleType = ntpath if _WINDOWS_ROOT.match(scratch_root) else posixpath
        self._scratch = str(self._flavor.normpath(scratch_root))

    @property
    def scratch_root(self) -> str:
        """The scratch root as normalised device text."""
        return self._scratch

    def resolve(self, path: Path) -> Path:
        """Return the device path `path` names, as a `Path` holding its normalised text.

        The `PathRule` a lease asks: a relative path is read under scratch and `.` and `..` are
        collapsed, with no read of any filesystem.
        """
        return Path(self.resolve_text(device_text(path)))

    def is_within(self, path: Path, root: Path) -> bool:
        """Whether `path` equals `root` or lies beneath it, in the device's case rules."""
        return self.within(device_text(root), device_text(path))

    def resolve_text(self, text: str) -> str:
        """Return the absolute, normalised device path `text` names (relative: under scratch)."""
        joined = text if self._flavor.isabs(text) else self._flavor.join(self._scratch, text)
        return str(self._flavor.normpath(joined))

    def normalise(self, text: str) -> str:
        """Return `text` with its separators and `.`/`..` segments in the device's normal form."""
        return str(self._flavor.normpath(text))

    def basename(self, text: str) -> str:
        """Return the last segment of a device path, in the device's own spelling."""
        return str(self._flavor.basename(text))

    def within(self, root: str, path: str) -> bool:
        """Whether `path` equals `root` or lies beneath it, comparing in the device's case rules."""
        normal_root = self._flavor.normcase(root)
        normal_path = self._flavor.normcase(path)
        try:
            return bool(self._flavor.commonpath([normal_root, normal_path]) == normal_root)
        except ValueError:
            # Different drives, or an absolute path against a relative one: nothing in common.
            return False


class DevicePaths:
    """Resolve and judge the paths of one lease on one device, by their text alone."""

    def __init__(self, lease: RealCellLease) -> None:
        """Build the path rules for `lease`'s scratch root.

        Args:
            lease: The lease whose scratch root and (live) allowed paths bound every path.
        """
        self._lease = lease
        self._rule = DevicePathRule(device_text(lease.scratch_root))

    @property
    def scratch_root(self) -> str:
        """The lease's scratch root as normalised device text."""
        return self._rule.scratch_root

    def resolve(self, path: Path | str) -> str:
        """Return the absolute, normalised device path `path` names.

        Args:
            path: A device path, absolute or relative to the scratch root.

        Returns:
            The path with a relative one joined under scratch and `.` and `..` collapsed.
        """
        return self._rule.resolve_text(device_text(path))

    def is_inside_scratch(self, resolved: str) -> bool:
        """Whether a resolved path is the scratch root or lies beneath it."""
        return self._rule.within(self._rule.scratch_root, resolved)

    def is_reachable(self, resolved: str) -> bool:
        """Whether a resolved path is inside scratch or under a path the lease allows right now."""
        if self.is_inside_scratch(resolved):
            return True
        # Read from the lease on every call: a lease widened after the session was built (a kept
        # root, a declared Leaving) must be honoured at once.
        return any(
            self._rule.within(self._rule.normalise(device_text(allowed)), resolved)
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
        return self._rule.basename(text)
