"""Resolve a request's paths and say what they need: scratch, outside scratch, and refusal.

A Real Cell is an existing device the Hive borrows for a task and leaves exactly as found, so
every path in a request is judged on what it *really* is, not on the text it was sent as
(gateway-contract section 12). A relative path is joined under the lease's scratch directory;
``.`` and ``..`` segments are removed and symbolic links are resolved *before* deciding whether
the path is inside scratch and which capability it needs, so a link inside scratch that points
outside is outside. The caller then acts on the resolved path it checked, never on the original
text. A path inside scratch needs only the capability for what is done to it; outside scratch a
read still needs ``fs:read`` while a write or delete also needs ``cell:outside_scratch``.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by the session, file,
    process and observe executors from ``prepare``; calls into pollen.executors.base and the
    standard library only.

Key invariants:
    - A path that cannot be resolved safely (a NUL byte, a symlink loop, an OS error) is refused;
      it is never guessed at.
    - The result is always absolute and has no symlink in its existing part.
    - A refusal names the lease, never the path.

See Also:
    - docs/waggle/gateway-contract.md sections 10 and 12.
    - packages/hivemind/src/hivemind/cell/session.py (resolve_scratch_path) for the semantics
      this matches.
"""

from __future__ import annotations

from pathlib import Path

from pollen.errors import PathRefusedError
from pollen.executors.base import FS_READ, FS_WRITE, OUTSIDE_SCRATCH, Need

__all__ = ["is_within", "read_needs", "resolve_path", "write_needs"]


def resolve_path(scratch_root: Path, text: str, lease_id: str) -> Path:
    """Resolve ``text`` against ``scratch_root``: ``.``, ``..`` and symlinks removed.

    Args:
        scratch_root: The lease's scratch directory; relative paths join under it.
        text: The path as the request wrote it.
        lease_id: The lease, for the refusal message.

    Returns:
        The absolute resolved path; it need not exist.

    Raises:
        PathRefusedError: The path is empty, has a NUL byte, loops through symlinks or cannot be
            resolved.
    """
    if not text:
        raise PathRefusedError(f"A request for lease {lease_id} named an empty path.")
    candidate = Path(text)
    joined = candidate if candidate.is_absolute() else scratch_root / candidate
    # strict=False resolves every existing symlink and normalises the rest, which is exactly the
    # "decide on what it really is" rule; a path that does not exist yet still resolves.
    try:
        return joined.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathRefusedError(
            f"A path in a request for lease {lease_id} cannot be resolved safely."
        ) from exc


def is_within(root: Path, path: Path) -> bool:
    """Return whether the resolved ``path`` is ``root`` itself or below it.

    Args:
        root: A resolved directory, normally the lease's scratch root.
        path: A resolved path.
    """
    return path == root or root in path.parents


def write_needs(resolved: Path, scratch_root: Path) -> tuple[Need, ...]:
    """Return what writing or deleting ``resolved`` needs.

    Args:
        resolved: The resolved path.
        scratch_root: The lease's resolved scratch directory.

    Returns:
        ``fs:write`` for the path, plus ``cell:outside_scratch`` when it is outside scratch.
    """
    needs = [Need(FS_WRITE, str(resolved))]
    if not is_within(scratch_root, resolved):
        needs.append(Need(OUTSIDE_SCRATCH, str(resolved)))
    return tuple(needs)


def read_needs(resolved: Path) -> tuple[Need, ...]:
    """Return what reading ``resolved`` needs: ``fs:read`` for the path, in or out of scratch."""
    return (Need(FS_READ, str(resolved)),)
