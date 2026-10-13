"""Look at the disk from async tests without blocking the loop by name: a few synchronous probes.

The end-to-end tests are coroutines, and the linter flags a file-system call made directly inside
one because on a real device it could stall the loop. In a test the disk is a local temporary
directory and the call takes microseconds, so what the tests need is not a thread hop but a way to
ask a plain question ("is this a directory?") without a lint suppression on every line. These
functions are that: each is an ordinary synchronous function, so a test calls it as an ordinary
expression. They are used only to inspect what a packet did, never to do the packet's work.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by the pollen end-to-end tests; depends on the
    standard library only.

Key invariants:
    - Every function only reads; none creates, changes or removes anything.

See Also:
    - scenarios.py for the helpers that speak to a packet.
"""

from __future__ import annotations

import stat
from pathlib import Path

__all__ = ["exists", "is_dir", "permission_bits", "resolved", "tree"]


def exists(path: Path) -> bool:
    """Return whether ``path`` exists."""
    return path.exists()


def is_dir(path: Path) -> bool:
    """Return whether ``path`` is a directory."""
    return path.is_dir()


def permission_bits(path: Path) -> int:
    """Return the permission bits of ``path`` (``0o700`` and the like)."""
    return stat.S_IMODE(path.stat().st_mode)


def resolved(path: Path) -> Path:
    """Return ``path`` with links and ``..`` resolved."""
    return path.resolve()


def tree(root: Path, pattern: str = "*") -> list[Path]:
    """Return everything under ``root`` that matches ``pattern``, at any depth."""
    return list(root.rglob(pattern))
