"""List what a generated tool may not import or call: the static gate's data, one reason apiece.

A tool the Royal Jelly Lab scaffolds is code a model wrote, so it is untrusted. The static gate
refuses, by reading its source, every module and name through which such code could reach past the
`CellSession` it is handed: the network, new processes, dynamic import and evaluation, raw file
access, signals, native code. A tool acts only through its session (codingrules section 4), so
none of these has a legitimate use in one. The lists are plain frozensets and mappings with a
one-line reason beside each entry, so a reviewer widening one sees what the entry is guarding. The
static gate is the first line only: the runner's audit hook watches the same classes of action at
run time, which is what catches whatever a source scan cannot see.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Read by `gates.static` (and re-exported from the Comb's face for the scaffolder's prompt).
    Calls into nothing; pure data.

Key invariants:
    - A module in `BANNED_MODULES` is banned together with every submodule of it.
    - `BANNED_NAMES` maps a module to attribute names banned on it, in every spelling the static
      gate can resolve (`os.system`, `from os import system`, `import os as o; o.system`).
    - What is not listed is allowed only if it is the standard library, `hivemind.cell` (for the
      session types) or the package's own two modules; the static gate enforces that separately.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the gate order and why static comes first.
    - hivemind.royal_jelly.quarantine_comb.runner.hooks for the run-time counterpart.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# Modules (and everything below them) a tool may never import.
BANNED_MODULES: frozenset[str] = frozenset(
    {
        "socket",  # Raw network sockets: the sandbox has no network and a tool has no business.
        "subprocess",  # Starting processes: a tool runs commands through `session.exec` only.
        "asyncio.subprocess",  # asyncio's process API is the same escape under another name.
        "ctypes",  # Native calls bypass every Python-level check.
        "multiprocessing",  # Forks and spawns processes outside the session.
        "importlib",  # Dynamic import hides what a tool really loads.
        "http",  # Network clients and servers.
        "urllib",  # Network access by URL.
        "urllib3",  # Third-party HTTP, same reason.
        "requests",  # Third-party HTTP, same reason.
        "httpx",  # Third-party HTTP, same reason.
        "ftplib",  # Network file transfer.
        "smtplib",  # Network mail.
        "telnetlib",  # Network shell.
        "xmlrpc",  # Network procedure calls.
        "ssl",  # TLS sockets.
        "pty",  # Pseudo-terminals spawn shells.
        "signal",  # Signals can kill the runner or the Warden's other processes.
        "shutil",  # Recursive deletion and copying outside the session's path checks.
        "posix",  # `os` without the names the checks below watch.
        "nt",  # The same on Windows.
        "runpy",  # Runs modules and files by name.
        "pkgutil",  # Imports modules by name.
        "zipimport",  # Imports code out of archives.
        "marshal",  # Loads raw code objects.
        "gc",  # Reaches every object in the runner, including the audit hook's state.
        "resource",  # A tool could lift the limits the runner set.
        "fcntl",  # Raw file-descriptor control.
        "mmap",  # Maps files and memory outside the session.
        "webbrowser",  # Launches processes.
        "pydoc",  # Imports and runs modules by name.
        "code",  # An interactive interpreter is dynamic evaluation.
        "codeop",  # Compiles source at run time.
        "pdb",  # A debugger runs arbitrary commands.
        "trace",  # Executes code under a tracer.
    }
)

# Attribute names banned on one module, however the module is reached.
BANNED_NAMES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "asyncio": frozenset(
            {
                "create_subprocess_exec",  # Starts a process outside the session.
                "create_subprocess_shell",  # Starts a shell.
                "subprocess",  # The asyncio process module.
            }
        ),
        "os": frozenset(
            {
                "system",  # Runs a shell command.
                "popen",  # Runs a shell command and pipes it.
                "posix_spawn",  # Starts a process.
                "posix_spawnp",  # Starts a process.
                "fork",  # Duplicates the process.
                "forkpty",  # Duplicates the process behind a terminal.
                "kill",  # Signals another process.
                "killpg",  # Signals a process group.
                "remove",  # Deletes outside the session's path checks.
                "unlink",  # Deletes outside the session's path checks.
                "rmdir",  # Deletes outside the session's path checks.
                "removedirs",  # Deletes outside the session's path checks.
                "chmod",  # Changes permissions outside the session's path checks.
                "chown",  # Changes ownership outside the session's path checks.
                "open",  # Raw file access outside the session's path checks.
                "fdopen",  # Raw file access outside the session's path checks.
            }
        ),
        "sys": frozenset(
            {
                "modules",  # Reaches every loaded module, including banned ones.
                "settrace",  # Hooks every line the runner executes.
                "setprofile",  # Hooks every call the runner makes.
                "addaudithook",  # Could add a hook that hides the runner's.
            }
        ),
        "builtins": frozenset(
            {
                "open",  # Raw file access outside the session's checks.
                "eval",  # Evaluates a string as code.
                "exec",  # Executes a string as code.
                "compile",  # Builds code from a string.
                "__import__",  # Dynamic import.
                "breakpoint",  # Drops into a debugger.
            }
        ),
        "io": frozenset({"open", "FileIO"}),  # Raw file access, a second spelling of `open`.
        "codecs": frozenset({"open"}),  # Raw file access, a third spelling.
        "tempfile": frozenset({"mktemp"}),  # A racy name-only temp path.
    }
)

# Name prefixes banned on one module: the `exec*` and `spawn*` families replace or start processes.
BANNED_NAME_PREFIXES: Mapping[str, tuple[str, ...]] = MappingProxyType({"os": ("exec", "spawn")})

# Builtins banned as bare names: the `builtins` entry above, as the static gate matches it.
BANNED_BUILTINS: frozenset[str] = BANNED_NAMES["builtins"]

# Attribute names banned on any object, because the object's type is not visible to a source scan.
BANNED_ANY_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "subprocess_exec",  # An event loop's process API.
        "subprocess_shell",  # An event loop's shell API.
        "create_connection",  # An event loop's network client.
        "create_server",  # An event loop's network server.
        "create_datagram_endpoint",  # An event loop's UDP socket.
        "open_connection",  # asyncio's stream client.
        "start_server",  # asyncio's stream server.
        "__builtins__",  # The builtins table, reached around the name checks.
        "__subclasses__",  # The classic walk to os and subprocess classes.
        "__globals__",  # A function's module namespace.
        "__loader__",  # A module's importer.
        "__reduce__",  # Builds objects from arbitrary callables.
    }
)

# Methods of `pathlib.Path` that touch the filesystem; the module is allowed for path arithmetic.
PATHLIB_IO_ATTRIBUTES: frozenset[str] = frozenset(
    {"open", "read_text", "read_bytes", "write_text", "write_bytes", "touch", "unlink", "rmdir"}
)

# The only non-standard-library module prefixes a tool may import: the session types.
ALLOWED_PREFIXES: tuple[str, ...] = ("hivemind.cell",)

__all__ = [
    "ALLOWED_PREFIXES",
    "BANNED_ANY_ATTRIBUTES",
    "BANNED_BUILTINS",
    "BANNED_MODULES",
    "BANNED_NAMES",
    "BANNED_NAME_PREFIXES",
    "PATHLIB_IO_ATTRIBUTES",
    "is_banned_module",
    "is_banned_name",
]


def is_banned_module(dotted: str) -> bool:
    """Return whether `dotted` is a banned module or lives below one.

    Args:
        dotted: A module path such as `urllib.parse`.

    Returns:
        True when the module, or any parent of it, is in `BANNED_MODULES`.
    """
    # Every parent is tried so `urllib.request` is caught by the `urllib` entry.
    parts = dotted.split(".")
    return any(".".join(parts[: i + 1]) in BANNED_MODULES for i in range(len(parts)))


def is_banned_name(module: str, name: str) -> bool:
    """Return whether attribute `name` is banned on `module`.

    Args:
        module: The module the attribute is read from (`os`).
        name: The attribute (`system`).

    Returns:
        True when `BANNED_NAMES` lists it or a `BANNED_NAME_PREFIXES` prefix starts it.
    """
    if name in BANNED_NAMES.get(module, frozenset()):
        return True
    return any(name.startswith(prefix) for prefix in BANNED_NAME_PREFIXES.get(module, ()))
