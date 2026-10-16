"""Watch a running tool with an audit hook: record the forbidden actions that are its own.

The static gate reads source; this module watches what the source actually does. Python's audit
hook (`sys.addaudithook`) is called for security-relevant events: a socket created or connected, a
process started, a program replaced, a file opened, a file renamed or linked. The hook records the
ones the sandbox forbids (network, new processes, native code loading, and file access outside the
package, the sandbox root and the interpreter's own prefixes) and then raises, which stops the
action. An event that names two paths (a rename, a hard link, a symlink, a copy) is judged on every
path it names: the source is what the tool reads, the destination is where the bytes end up, and
a move from scratch to anywhere else is the escape the Comb exists to catch. The subtle part
is attribution (ADR-0047): the tool calls `session.exec`, and the session's own implementation
starts a process, so "a process started" is not by itself the tool's doing. An event counts
against the tool only when the innermost frame that is not the standard library is inside the
package directory: then the tool's own line is the one asking. A frame in `hivemind/cell` between
them means the session did it, which is the allowed path. This is a Python-level control and not a
security boundary: native extensions are not seen, which is why dependencies are refused.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Installed by `runner.main` before the tool loads; read back into the runner's report as
    `violations`. The one place besides `usage` that touches interpreter internals (`sys._getframe`,
    `sys.addaudithook`). Calls into the standard library only.

Key invariants:
    - `classify` is pure: an event, its arguments and the frame filenames in, a hit (or None) out.
    - An event is blamed on the tool only through the innermost-non-stdlib-frame rule above;
      a thread with no such frame at all is blamed only for process creation.
    - The hook raises `SandboxViolationError` after recording, so a forbidden action never happens.
    - Recording is bounded and the hook never recurses into itself.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the attribution rule.
    - hivemind.royal_jelly.quarantine_comb.gates.banned for the static counterpart.
"""

from __future__ import annotations

import os
import socket
import sys
import sysconfig
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import FrameType, MappingProxyType

from hivemind.royal_jelly.quarantine_comb.runner.report import MAX_LIST_ITEMS, Violation

# Events that start a process or replace the program: always forbidden to the tool.
_PROCESS_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
    }
)
_NATIVE_EVENTS = frozenset(
    {"ctypes.dlopen"}
)  # Loading a shared library escapes every Python check.
# Events that name paths the tool must keep inside the allowed roots, with the positions of
# every path among the event's arguments (CPython's audit event table). An event with two is the
# move-or-link family: its second path is a destination, and that is where a file escapes to.
# `os.replace`, `shutil.move` and `pathlib.Path.rename` all end in the `os.rename` event;
# `os.makedirs` is a run of `os.mkdir` events; `shutil` is banned statically but its own events
# are listed too, so a run-time reach through it (a `getattr`, an alias) is still judged.
_PATH_ARGUMENTS: Mapping[str, tuple[int, ...]] = MappingProxyType(
    {
        "open": (0,),
        "os.listdir": (0,),
        "os.scandir": (0,),
        "os.remove": (0,),
        "os.rmdir": (0,),
        "os.mkdir": (0,),
        "os.chmod": (0,),
        "os.chown": (0,),
        "os.chflags": (0,),
        "os.truncate": (0,),
        "os.utime": (0,),
        "os.walk": (0,),
        "os.fwalk": (0,),
        "os.getxattr": (0,),
        "os.listxattr": (0,),
        "os.setxattr": (0,),
        "os.removexattr": (0,),
        "glob.glob": (0,),
        "tempfile.mkstemp": (0,),
        "tempfile.mkdtemp": (0,),
        "shutil.rmtree": (0,),
        "shutil.chown": (0,),
        "os.rename": (0, 1),
        "os.link": (0, 1),
        "os.symlink": (0, 1),
        "shutil.copyfile": (0, 1),
        "shutil.copymode": (0, 1),
        "shutil.copystat": (0, 1),
        "shutil.copytree": (0, 1),
        "shutil.move": (0, 1),
        "shutil.unpack_archive": (0, 1),
        "shutil.make_archive": (0, 2, 3),
    }
)
_INET_FAMILIES = (socket.AF_INET, socket.AF_INET6)  # A socket of these families is network access.
# Paths every program may open, as `_event_paths` renders them: reading the null device reveals
# nothing (`subprocess` opens it for DEVNULL); on Windows it resolves to `\\\\.\\NUL`.
_ALWAYS_READABLE = frozenset({"/dev/null", os.path.realpath(os.devnull)})
_MAX_PATH_CHARS = 200  # How much of an offending path the report keeps.

__all__ = [
    "AuditPolicy",
    "AuditRecorder",
    "Hit",
    "SandboxViolationError",
    "build_policy",
    "classify",
    "install_audit_hook",
]


class SandboxViolationError(Exception):
    """Raise inside the tool when it attempts a forbidden action; the action does not happen."""


@dataclass(frozen=True, slots=True)
class AuditPolicy:
    """What the hook compares events and frames against, all as resolved absolute paths.

    Attributes:
        package_dir: The tool's own directory; a frame inside it is the tool's.
        allowed_roots: Directories the tool may open files under.
        stdlib_roots: Directories whose frames are standard-library code, to be looked through.
    """

    package_dir: str
    allowed_roots: tuple[str, ...]
    stdlib_roots: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Hit:
    """One forbidden action, before the recorder stamps the phase on it."""

    kind: str
    detail: str
    location: str


def build_policy(package_dir: Path, sandbox_root: Path) -> AuditPolicy:
    """Collect the directories the hook treats as the tool's own, allowed or standard library.

    Args:
        package_dir: Where the tool's files were put.
        sandbox_root: The sandbox directory (scratch lives below it).

    Returns:
        The resolved policy for this interpreter.
    """
    paths = sysconfig.get_paths()
    stdlib = {paths["stdlib"], paths["platstdlib"], str(Path(os.__file__).parent)}
    prefixes = {sys.prefix, sys.base_prefix, sys.exec_prefix, *paths.values()}
    # The Hive's own packages may live outside every prefix (an editable install); a tool that
    # lazily imports `hivemind.cell` reads them, so their directories are readable too.
    import hivemind  # Late: only the runner needs the package location.
    import waggle  # Late, for the same reason.

    own = {str(Path(module.__file__).parent) for module in (hivemind, waggle) if module.__file__}
    roots = {str(package_dir), str(sandbox_root), *prefixes, *own}
    return AuditPolicy(
        package_dir=os.path.realpath(package_dir),
        allowed_roots=tuple(sorted(os.path.realpath(root) for root in roots)),
        stdlib_roots=tuple(sorted(os.path.realpath(root) for root in stdlib)),
    )


def classify(
    event: str,
    args: tuple[object, ...],
    frames: Iterable[tuple[str, int]],
    policy: AuditPolicy,
    *,
    in_thread: bool = False,
) -> Hit | None:
    """Decide whether an audit event is a forbidden action by the tool.

    Args:
        event: The audit event name (`subprocess.Popen`, `open`, ...).
        args: The event's arguments as Python passed them.
        frames: `(filename, line)` of each frame, innermost first; consumed lazily.
        policy: The directories to judge against.
        in_thread: Whether the event happened outside the main thread.

    Returns:
        A hit when the event is forbidden and the tool is responsible; otherwise None.
    """
    what = _forbidden_what(event, args, policy)
    if what is None:
        return None
    # Only now is the (comparatively costly) stack read: most events are not forbidden at all.
    owner = _owner(frames, policy)
    if owner is None:
        # No frame outside the standard library: a thread started by the tool's code loses its
        # stack. Only process creation is blamed on it; anything else would catch asyncio itself.
        return (
            Hit(what[0], what[1], "a worker thread")
            if in_thread and event in _PROCESS_EVENTS
            else None
        )
    return Hit(what[0], what[1], owner) if owner else None


def _forbidden_what(
    event: str, args: tuple[object, ...], policy: AuditPolicy
) -> tuple[str, str] | None:
    """Return `(kind, detail)` if the event is forbidden regardless of who caused it."""
    if event in _PROCESS_EVENTS:
        return "process", f"started a process or shell ({event}): {_first_word(args)}"
    if event in _NATIVE_EVENTS:
        return "native", f"loaded a shared library ({event})"
    if event.startswith("socket."):
        # asyncio makes AF_UNIX socket pairs for its own wake-up; only network families count
        # (on Windows the pair is AF_INET over loopback, and the recorder exempts it by frame).
        if event == "socket.__new__" and (len(args) < 2 or args[1] not in _INET_FAMILIES):
            return None
        return "socket", f"used the network ({event})"
    # Every path the event names is judged, so a rename or link whose source is in scratch and
    # whose destination is not is caught on the destination.
    for path in _event_paths(event, args):
        if not _is_allowed(path, policy):
            return (
                "open",
                f"touched {path[:_MAX_PATH_CHARS]} outside the package and scratch ({event})",
            )
    return None


def _owner(frames: Iterable[tuple[str, int]], policy: AuditPolicy) -> str | None:
    """Name the tool's frame (`file:line`) that owns the event.

    Returns `""` when the innermost non-stdlib frame is not the tool's, and None when no
    frame is outside the standard library.
    """
    for filename, line in frames:
        if _is_stdlib(filename, policy):
            continue
        inside = filename.startswith(policy.package_dir + os.sep)
        return f"{os.path.basename(filename)}:{line}" if inside else ""
    return None


def _is_stdlib(filename: str, policy: AuditPolicy) -> bool:
    """Return whether a frame's file is standard-library code (or frozen bootstrap code)."""
    if filename.startswith("<frozen"):
        return True
    if "site-packages" in filename or "dist-packages" in filename:
        return False
    return any(filename.startswith(root + os.sep) for root in policy.stdlib_roots)


def _is_allowed(path: str, policy: AuditPolicy) -> bool:
    """Return whether an absolute resolved path lies under an allowed root."""
    if path in _ALWAYS_READABLE:
        return True
    return any(path == root or path.startswith(root + os.sep) for root in policy.allowed_roots)


def _event_paths(event: str, args: tuple[object, ...]) -> Iterator[str]:
    """Yield every path argument of a path event, resolved; one that is no path is skipped."""
    for index in _PATH_ARGUMENTS.get(event, ()):
        text = _as_text(args[index]) if index < len(args) else None
        if text is None:
            continue  # A file descriptor or None: nothing to judge.
        # A symlink's target is a name the kernel resolves relative to the link's own directory,
        # not the working directory, so it is anchored there before it is judged; otherwise a
        # tool could point a link in scratch at anything with `../`.
        if event == "os.symlink" and index == 0 and not os.path.isabs(text):
            link = _as_text(args[1]) if len(args) > 1 else None
            text = os.path.join(os.path.dirname(link), text) if link else text
        yield os.path.realpath(text)


def _as_text(raw: object) -> str | None:
    """Return an event's path argument as text, or None when it is not a path at all."""
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    elif isinstance(raw, os.PathLike):
        raw = os.fspath(raw)
    return raw if isinstance(raw, str) and raw else None


def _first_word(args: tuple[object, ...]) -> str:
    """Name the program a process event is about, as short text."""
    argv = args[1] if len(args) > 1 else args[0] if args else ""
    first = argv[0] if isinstance(argv, list | tuple) and argv else argv
    return str(first)[:_MAX_PATH_CHARS]


class AuditRecorder:
    """The audit hook itself: classifies events, records hits and raises to stop them.

    Mutable state owned here alone (codingrules 8.5): the current phase (set by the runner as it
    moves on), the recorded violations and a per-thread re-entry guard. `__call__` is what
    `sys.addaudithook` registers; it must never raise anything but `SandboxViolationError`.
    """

    def __init__(self, policy: AuditPolicy) -> None:
        """Start recording against `policy`, in the `load` phase."""
        self.policy = policy
        self.phase = "load"
        self.violations: list[Violation] = []
        self._guard = threading.local()  # Per-thread: the hook's own work must not be audited.

    def __call__(self, event: str, args: tuple[object, ...]) -> None:
        """Handle one audit event; raise `SandboxViolationError` if it is the tool's violation."""
        if getattr(self._guard, "busy", False):
            return
        self._guard.busy = True
        try:
            start = sys._getframe(1)  # The frame that triggered the event.
            if event.startswith("socket.") and _inside_socketpair(start):
                return  # The event loop's own wake-up pair, never the tool's network use.
            hit = classify(
                event,
                args,
                _frames(start),
                self.policy,
                in_thread=threading.current_thread() is not threading.main_thread(),
            )
        finally:
            self._guard.busy = False
        if hit is None:
            return
        if len(self.violations) < MAX_LIST_ITEMS:
            self.violations.append(
                Violation(kind=hit.kind, detail=hit.detail, phase=self.phase, location=hit.location)
            )
        raise SandboxViolationError(hit.detail)


def install_audit_hook(recorder: AuditRecorder) -> None:
    """Register `recorder` as an audit hook; it cannot be removed for the life of the process."""
    sys.addaudithook(recorder)


# Where `socket.socketpair` is Python code (Windows), the code object of that function; asyncio
# makes its wake-up pair through it from two loopback AF_INET sockets, which look like network
# use to the family check. Elsewhere the pair is AF_UNIX from C and the family check exempts it.
_SOCKETPAIR_CODE = getattr(socket.socketpair, "__code__", None)


def _inside_socketpair(start: FrameType | None) -> bool:
    """Return whether a frame from `start` outward is `socket.socketpair`'s own Python body."""
    if _SOCKETPAIR_CODE is None:
        return False
    frame = start
    while frame is not None:
        if frame.f_code is _SOCKETPAIR_CODE:
            return True
        frame = frame.f_back
    return False


def _frames(start: FrameType | None) -> Iterator[tuple[str, int]]:
    """Walk the call stack outward from `start`, yielding each frame's file and line."""
    frame = start
    while frame is not None:
        yield frame.f_code.co_filename, frame.f_lineno
        frame = frame.f_back
