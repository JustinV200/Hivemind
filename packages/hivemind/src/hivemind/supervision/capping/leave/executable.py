"""Define looks_executable: a pure, conservative guess at whether a path is an executable.

Roadmap step 5.0c: "whether the file is executable" is one of `decide`'s already-known inputs.
Neither `hivemind.cell.CellSession` nor a unified diff carries a POSIX permission bit, so this
module never claims to read one; it guesses from the path's own suffix (a fixed allowlist per
`hivemind.cell.OsFamily`) and, for POSIX, from a `#!` shebang at the start of the content actually
about to be written -- both facts already in hand by the time `hivemind.supervision.capping.apply`
calls this (the resolved path, and the new content a diff produces), so the function itself stays
pure (codingrules section 8.3): no I/O, `content` is handed in already read.

A conservative guess errs toward "yes": `hivemind.supervision.capping.leave.policy.decide` sends
an executable to ASK rather than ALLOW (roadmap step 5.0c's own suggested default table), so a
false positive here only costs an extra question, never a silent persist of something dangerous;
a false negative would not.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. Called by `hivemind.supervision.capping.apply` once per outside-scratch path, building
    `hivemind.supervision.capping.leave.model.LeaveRequest.is_executable`. Calls into `hivemind.
    cell` (OsFamily) and the standard library only.

Key invariants:
    - Suffix comparison is always case-insensitive (`.EXE` counts on Windows, `.SH` on POSIX):
      filesystems on both families commonly preserve case without enforcing it.
    - The shebang check only ever applies on POSIX (Windows never treats `#!` as meaningful).

See Also:
    - .claude/roadmap.md step 5.0c for "whether the file is executable" as a decide() input.
    - .claude/codingrules.md section 8.3 for "pure core, effectful edges."
    - hivemind.supervision.capping.leave.policy for decide, this fact's one reader.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from hivemind.cell import OsFamily

# Windows: extensions the shell or a scheduled task can execute directly.
_WINDOWS_EXECUTABLE_SUFFIXES: frozenset[str] = frozenset(
    {".exe", ".bat", ".cmd", ".com", ".ps1", ".msi", ".scr", ".vbs", ".js"}
)
# POSIX: common script extensions; a bare shebang (checked separately) covers an extensionless
# script, which is the normal POSIX convention for anything actually chmod +x'd.
_POSIX_EXECUTABLE_SUFFIXES: frozenset[str] = frozenset(
    {".sh", ".bash", ".zsh", ".py", ".pl", ".rb"}
)
_SHEBANG_PREFIX = b"#!"  # The first two bytes of a POSIX script that names its own interpreter.

__all__ = ["looks_executable"]


def looks_executable(path: str, os_family: OsFamily, content: bytes) -> bool:
    """Guess, conservatively, whether `path` (about to hold `content`) is an executable.

    Args:
        path: The resolved path, as text, in `os_family`'s own separator style.
        os_family: Which OS the owning Cell runs; chooses the suffix allowlist.
        content: The bytes about to be written at `path` (already read/computed by the caller).

    Returns:
        True when the suffix matches a known executable extension for `os_family`, or (POSIX
        only) `content` starts with a `#!` shebang.
    """
    suffix = _suffix(path, os_family).lower()
    if os_family is OsFamily.WINDOWS:
        return suffix in _WINDOWS_EXECUTABLE_SUFFIXES
    if suffix in _POSIX_EXECUTABLE_SUFFIXES:
        return True
    return content.startswith(_SHEBANG_PREFIX)


def _suffix(path: str, os_family: OsFamily) -> str:
    """Return `path`'s own file extension, parsed with the flavour matching `os_family`."""
    flavor = PureWindowsPath if os_family is OsFamily.WINDOWS else PurePosixPath
    return flavor(path).suffix
