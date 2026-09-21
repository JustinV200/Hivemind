"""Define declared_leaving_root and scan_declared_leaves: the run_command before/after scan.

Roadmap step 5.0e: "`run_command` effects cannot carry a restore record, so v0 scans only the
task's declared `leaves` patterns before and after each command and ledgers what appeared or
changed there; anything else a command touches stays a `cell.touched_outside_scratch` event as
today." A DIFF or COPY action already knows the one path it is about to write before it writes it
(`hivemind.supervision.capping.apply._apply_diff`/`._apply_copy` record a restore path *before*
the write); a COMMAND is a black box until it exits, so this module is the only way `hivemind.
supervision.capping.apply._apply_command` can even find out what a command wrote outside scratch.

`declared_leaving_root` turns one `PlannedLeaving.pattern` into the widest literal directory the
pattern could ever match: everything up to (not including) its first `*`/`**` segment, after `~`
expansion. It is the same conservative root `hivemind.wardens.spawn.spawn._widen_lease_
reachability` needs to widen a lease's own reachable paths, so both live here rather than each
reimplementing it. `scan_declared_leaves` walks every declared root, hashing every regular file it
finds (never following a symlink, at any depth, mirroring `hivemind.cell.local.quota.
directory_size_bytes`), bounded by `MAX_SCAN_FILES`/`MAX_SCAN_FILE_BYTES` so one command can never
make this scan hold an unbounded amount of memory or take an unbounded amount of time.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    leave`. `declared_leaving_root` is called by `hivemind.wardens.spawn.spawn` (Layer 5) and by
    this module's own `scan_declared_leaves`; `scan_declared_leaves` is called by `hivemind.
    supervision.capping.apply._apply_command`, once before running a COMMAND action and once
    after. Calls into `waggle.messages` (PlannedLeaving) and the standard library only.

Key invariants:
    - `scan_declared_leaves` never follows a symlink, whether the root itself or anything found
      while walking it: a command cannot make this scan read or ledger a file outside the
      declared root by linking to it (mirrors `hivemind.cell.local.quota.directory_size_bytes`'s
      own rule for the same reason).
    - A file over `MAX_SCAN_FILE_BYTES`, or found once `MAX_SCAN_FILES` is already reached, is
      silently left out of the result rather than raising: v0's own bound, not a scan failure: a
      command that legitimately wrote more than this is diagnosed by its own postconditions, not
      by this best-effort ledger.
    - `ScannedFile.content` is kept in memory (never just a digest) because a later apply needs
      the *prior* bytes for `RestoreRecord.prior`, not only whether something changed; the caps
      above exist specifically to keep that bounded (64 files of 1 MiB is 64 MiB worst case, held
      only for the duration of one command).

See Also:
    - .claude/roadmap.md step 5.0e for this module's own requirement, verbatim.
    - hivemind.cell.local.quota for directory_size_bytes, the symlink-safe walk this mirrors.
    - hivemind.supervision.capping.leave.matcher for matches_leaving, the sibling `~`-expansion
      convention this module's own `declared_leaving_root` follows.
    - hivemind.supervision.capping.apply for _apply_command, this module's one effectful caller.
    - hivemind.wardens.spawn.spawn for _widen_lease_reachability, `declared_leaving_root`'s other
      caller.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from waggle.messages import PlannedLeaving

MAX_SCAN_FILES = 64  # A generous handful of declared artefacts; matches MAX_LEAVES_ITEMS's order
# of magnitude (waggle.messages.task.assignment) -- a command legitimately producing more than
# this under a declared root is unusual for v0's own "install X"/"set up a project in Y" examples.
MAX_SCAN_FILE_BYTES = 1_048_576  # 1 MiB per file: held in memory for both scans at once (module
# docstring's own "Key invariants"), so the worst case (MAX_SCAN_FILES at this cap) stays a
# bounded 64 MiB rather than growing with whatever a command happens to write.

__all__ = [
    "MAX_SCAN_FILES",
    "MAX_SCAN_FILE_BYTES",
    "ScannedFile",
    "declared_leaving_root",
    "scan_declared_leaves",
]


@dataclass(frozen=True, slots=True)
class ScannedFile:
    """One file `scan_declared_leaves` found: its resolved path and its own bytes at scan time."""

    path: Path  # Resolved (absolute, ".."-free, symlink-free) path.
    content: bytes  # Read once, at scan time; never re-read (module docstring's own reason).


def declared_leaving_root(pattern: str, home: Path) -> Path:
    """Return the widest literal directory `pattern` could ever match.

    Expands a leading `~` against `home` (matching `hivemind.supervision.capping.leave.matcher`'s
    own expansion), then keeps every path segment up to the first one containing a `*` -- the
    conservative root `hivemind.supervision.capping.leave.matcher.matches_leaving`'s own glob
    semantics guarantee covers everything the pattern could match, since neither `*` nor `**`
    ever narrows what a segment before it means. A pattern with no `*` at all (the common case: a
    plain directory or file) is its own root, matching the matcher's "no `*` in the pattern" rule.

    Args:
        pattern: One `PlannedLeaving.pattern`, already known well-formed by its own validator
            (absolute or `~`-rooted, never a bare root/drive/home, never a `..` segment).
        home: The Cell's own home directory, for `~`-rooted pattern expansion.

    Returns:
        The literal directory (or file) path every match for `pattern` falls under.
    """
    expanded = pattern.replace("~", str(home), 1) if pattern.startswith("~") else pattern
    literal_parts: list[str] = []
    for part in Path(expanded).parts:
        if "*" in part:
            break
        literal_parts.append(part)
    return Path(*literal_parts) if literal_parts else Path(expanded)


def scan_declared_leaves(
    declared: tuple[PlannedLeaving, ...], home: Path
) -> dict[Path, ScannedFile]:
    """Walk every declared leaving's own root, hashing every regular file found.

    Args:
        declared: The task's own `TaskAssign.leaves`; empty scans nothing.
        home: The Cell's own home directory, for `~`-rooted pattern expansion.

    Returns:
        Every file found, keyed by its own resolved path, bounded by `MAX_SCAN_FILES` (in
        declaration order: a later root stops contributing once the bound is already reached).
    """
    found: dict[Path, ScannedFile] = {}
    for leaving in declared:
        if len(found) >= MAX_SCAN_FILES:
            break
        _scan_root(declared_leaving_root(leaving.pattern, home), found)
    return found


def _scan_root(root: Path, found: dict[Path, ScannedFile]) -> None:
    """Add every regular file under `root` (or `root` itself, if it is one) to `found`."""
    # SAFETY: `is_symlink` on the root itself, then `followlinks=False` on the walk below -- a
    # command cannot smuggle a file outside its declared root into this scan by linking to it.
    if root.is_symlink():
        return
    if root.is_file():
        _record(root, found)
        return
    if not root.is_dir():
        return  # Nothing there yet (a command that has not run, or never wrote anything here).
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for filename in filenames:
            if len(found) >= MAX_SCAN_FILES:
                return
            _record(Path(dirpath) / filename, found)


def _record(path: Path, found: dict[Path, ScannedFile]) -> None:
    """Read `path` and add it to `found`, skipping a symlink, a vanished file or an oversize one."""
    if path.is_symlink():
        return
    try:
        data = path.read_bytes()
    except OSError:
        return  # Vanished (or unreadable) between listing and reading; not a scan failure.
    if len(data) > MAX_SCAN_FILE_BYTES:
        return  # Too large for v0's own bound (module docstring); left out, not ledgered.
    resolved = path.resolve(strict=False)
    found[resolved] = ScannedFile(path=resolved, content=data)
