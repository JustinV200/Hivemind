r"""Define classify_path: pure classification of a resolved path into a PathClass.

Roadmap step 5.0c: "the path's class (keep_root, home, system or startup location)." Pure text
work over `PureWindowsPath`/`PurePosixPath` (codingrules 5: "never the host's Path" for inspecting
a pattern that may belong to a different OS than the one running this code -- the same reason
`hivemind.queen.planner.schema._pattern_reaches_into_scratch` never builds a bare `pathlib.Path`
either), chosen by `os_family` rather than by whatever OS actually runs this test or process, so a
Windows Cell's paths classify correctly from a POSIX Hive Stand and vice versa, and so the test
suite can exercise every path class on both OS families without needing two physical machines.

Precedence, most to least specific (a test asserts this order): `keep_root` first (an explicit,
narrower root inside which everything else stops mattering), then `startup`, then `system`, then
`home`; anything left over is `other`. `startup` locations are checked from known suffixes and
roots per `os_family` (roadmap step 5.0c's own list: Windows Startup folders and Run-key-equivalent
directories, `~/.config/autostart`, systemd user/system unit dirs, `/etc/init.d`, cron dirs, shell
rc/profile files, LaunchAgents/LaunchDaemons); `system` from a short list of well-known roots
(`/etc`, `/usr`, `/bin`, `/opt`, `C:\\Windows`, `C:\\Program Files*`).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. Called by `hivemind.supervision.capping.apply` once per outside-scratch path, before
    `hivemind.supervision.capping.leave.policy.decide`. Calls into `hivemind.cell` (OsFamily) and
    the standard library only.

Key invariants:
    - Every comparison goes through `PureWindowsPath`/`PurePosixPath`, chosen once by `os_family`,
      never the host's own concrete `pathlib.Path`: this module's own tests run on whichever OS CI
      happens to use and still exercise every `PathClass` on both families.
    - `PureWindowsPath` equality and `is_relative_to` are already case-insensitive (pathlib's own
      behaviour): no extra lower-casing is needed for the Windows family's own comparisons.
    - `keep_root=None` (roadmap step 5.0c: "the policy takes the keep root as an optional input,
      None today") simply never matches; every other path still classifies normally.

See Also:
    - .claude/roadmap.md step 5.0c for the path-class list and startup/system location list,
      verbatim.
    - hivemind.queen.planner.schema for the PureWindowsPath/PurePosixPath convention this module
      follows for inspecting a not-necessarily-this-host path.
    - hivemind.supervision.capping.leave.model for PathClass.
    - hivemind.supervision.capping.leave.policy for decide, this function's one caller's caller.
"""

from __future__ import annotations

from pathlib import PurePath, PurePosixPath, PureWindowsPath

from hivemind.cell import OsFamily
from hivemind.supervision.capping.leave.model import PathClass

# Windows: the per-user and all-users Startup folders (the directory-based equivalent of a Run
# registry key -- roadmap step 5.0c names both; this module only ever sees filesystem paths, so
# the registry key itself is never checked here).
_WINDOWS_STARTUP_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs", "Startup"),
    ("ProgramData", "Microsoft", "Windows", "Start Menu", "Programs", "StartUp"),
)
# Windows: the system tree itself (roadmap 5.0c: "C:\\Windows, C:\\Program Files*"), matched as
# the first segment after the drive so any drive letter works.
_WINDOWS_SYSTEM_FIRST_SEGMENTS: tuple[str, ...] = (
    "Windows",
    "Program Files",
    "Program Files (x86)",
)

# POSIX (Linux and macOS share these): systemd/cron/init locations, absolute (roadmap 5.0c).
_POSIX_STARTUP_ROOTS: tuple[str, ...] = (
    "/etc/systemd/system",
    "/etc/systemd/user",
    "/etc/init.d",
    "/etc/cron.d",
    "/etc/cron.daily",
    "/etc/cron.hourly",
    "/etc/cron.monthly",
    "/etc/cron.weekly",
    "/var/spool/cron",
)
# POSIX: home-relative startup locations (XDG autostart, a user's own systemd unit dir).
_POSIX_STARTUP_HOME_SUFFIXES: tuple[tuple[str, ...], ...] = (
    (".config", "autostart"),
    (".config", "systemd", "user"),
)
# POSIX: shell rc/profile files, matched by filename directly under home (roadmap 5.0c).
_POSIX_STARTUP_HOME_FILES: frozenset[str] = frozenset(
    {".bashrc", ".bash_profile", ".bash_login", ".zshrc", ".zprofile", ".profile"}
)
# macOS: LaunchAgents/LaunchDaemons, system-wide and per-user (roadmap 5.0c names both).
_MACOS_STARTUP_ROOTS: tuple[str, ...] = (
    "/Library/LaunchAgents",
    "/Library/LaunchDaemons",
    "/System/Library/LaunchAgents",
)
_MACOS_STARTUP_HOME_SUFFIXES: tuple[tuple[str, ...], ...] = (("Library", "LaunchAgents"),)

# POSIX system roots, shared by Linux and macOS (roadmap 5.0c: "/etc, /usr, /bin, /opt").
_POSIX_SYSTEM_ROOTS: tuple[str, ...] = ("/etc", "/usr", "/bin", "/sbin", "/opt")
_MACOS_SYSTEM_ROOTS: tuple[str, ...] = ("/System", "/Library", "/Applications")

__all__ = ["classify_path"]


def classify_path(path: str, os_family: OsFamily, home: str, keep_root: str | None) -> PathClass:
    """Classify a resolved path into keep_root, startup, system, home or other.

    Args:
        path: The resolved, absolute path as text, in `os_family`'s own separator style.
        os_family: Which OS the owning Cell runs; chooses the pure-path flavour and the startup/
            system location lists.
        home: The Cell's own home directory, as text.
        keep_root: The manifest's `[hive_stand] keep_root`, as text, or None (roadmap step 5.0c:
            "an optional input, None today").

    Returns:
        The path's PathClass, most-specific match first (module docstring).
    """
    flavor = _flavor(os_family)
    candidate = flavor(path)
    if keep_root is not None and _is_within(candidate, flavor(keep_root)):
        return PathClass.KEEP_ROOT
    if _is_startup(candidate, os_family, flavor(home)):
        return PathClass.STARTUP
    if _is_system(candidate, os_family):
        return PathClass.SYSTEM
    if _is_within(candidate, flavor(home)):
        return PathClass.HOME
    return PathClass.OTHER


def _flavor(os_family: OsFamily) -> type[PurePath]:
    """Return the PurePath subclass matching `os_family` (module docstring's own convention)."""
    return PureWindowsPath if os_family is OsFamily.WINDOWS else PurePosixPath


def _is_within(path: PurePath, root: PurePath) -> bool:
    """Return whether `path` equals `root` or is somewhere underneath it."""
    return path == root or root in path.parents


def _contains_suffix(path: PurePath, suffix: tuple[str, ...]) -> bool:
    """Return whether `suffix` appears, in order, as a contiguous run anywhere in `path`'s parts.

    Matches both the named directory itself (its own final parts equal `suffix`) and anything a
    task wrote inside it (`suffix` sits partway through `path`'s parts, with more segments after
    it) -- a leaving is almost always a *file placed inside* a startup directory, not the
    directory itself.
    """
    parts = path.parts
    span = len(suffix)
    return any(parts[i : i + span] == suffix for i in range(len(parts) - span + 1))


def _is_startup(candidate: PurePath, os_family: OsFamily, home: PurePath) -> bool:
    """Return whether `candidate` is a known startup location for `os_family`."""
    if os_family is OsFamily.WINDOWS:
        return any(_contains_suffix(candidate, suffix) for suffix in _WINDOWS_STARTUP_SUFFIXES)
    # POSIX (Linux and macOS): absolute roots, home-relative suffixes, and shell rc/profile files.
    posix_roots = (
        *_POSIX_STARTUP_ROOTS,
        *(_MACOS_STARTUP_ROOTS if os_family is OsFamily.MACOS else ()),
    )
    if any(_is_within(candidate, PurePosixPath(root)) for root in posix_roots):
        return True
    home_suffixes = (
        *_POSIX_STARTUP_HOME_SUFFIXES,
        *(_MACOS_STARTUP_HOME_SUFFIXES if os_family is OsFamily.MACOS else ()),
    )
    if any(_contains_suffix(candidate, suffix) for suffix in home_suffixes):
        return True
    return candidate.parent == home and candidate.name in _POSIX_STARTUP_HOME_FILES


def _is_system(candidate: PurePath, os_family: OsFamily) -> bool:
    """Return whether `candidate` is a known system location for `os_family`."""
    if os_family is OsFamily.WINDOWS:
        parts = candidate.parts
        # parts[0] is the drive ("C:\\"); the system trees live at the first segment after it.
        return len(parts) >= 2 and any(
            parts[1].lower() == segment.lower() for segment in _WINDOWS_SYSTEM_FIRST_SEGMENTS
        )
    posix_roots = (
        *_POSIX_SYSTEM_ROOTS,
        *(_MACOS_SYSTEM_ROOTS if os_family is OsFamily.MACOS else ()),
    )
    return any(_is_within(candidate, PurePosixPath(root)) for root in posix_roots)
