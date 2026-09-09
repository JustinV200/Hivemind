"""Define ScratchQuota and directory_size_bytes: how a lease's scratch directory is capped.

The Hive Stand (the machine the Queen runs on) is the operator's own device, so a runaway or
malicious command must never be allowed to fill its disk. `ScratchQuota` is the one number that
matters: the most bytes a single lease's scratch directory (its own subdirectory of `[hive_stand]
scratch_root`) may hold before `LocalProcessSession`'s watchdog kills the running command.
`directory_size_bytes` is how that figure is measured by default: a plain recursive walk with
`os.scandir` that never follows a symlink, so a lease cannot inflate or hide its true usage by
linking to something outside scratch. `DirectorySizer` is the callable shape that measurement
takes; `ScratchQuota.sizer` carries one rather than the watchdog calling `directory_size_bytes`
directly, so a test can substitute a controllable sizer instead of racing a real subprocess's
write speed against the watchdog's own poll interval -- real cross-process file-size polling is
not reliable everywhere while a write is in flight (Windows in particular can report a directory
entry's cached, stale size for a file another process still holds open), so
`test_a_nested_archive_extraction_over_quota_is_stopped_and_cleaned_up` drives the crossing itself
through an injected sizer rather than asserting on exactly how many bytes leaked through before a
real poll happened to notice. `QUOTA_SAMPLE_INTERVAL_S` is the watchdog's tick cadence, named here
because it is a quota concern even though `session.py` is the module that runs the loop.

Fits into the Hive:
    Layer 2 (the Cell abstraction), inside `hivemind.cell.local` (the Hive Stand). Read by
    `hivemind.cell.local.session.LocalProcessSession`'s watchdog and by
    `hivemind.cell.local.source.HiveStandSource.open_session`, which sizes a `ScratchQuota` from
    `HiveStandConfig.scratch_quota_mb`. Calls into the standard library only.

Key invariants:
    - `directory_size_bytes` never follows a symlink, at any depth: a linked-to file's size is
      never counted, and a linked-to directory is never descended into.
    - `directory_size_bytes` treats a vanished directory (removed between the watchdog's tick and
      this call) as zero bytes rather than raising, since a command's own cleanup can race the
      watchdog harmlessly.
    - `ScratchQuota.sizer` defaults to `directory_size_bytes`; the watchdog always reads
      `quota.sizer`, never the module-level function by name, so every caller that does not
      override it gets the real measurement for free.

See Also:
    - .claude/roadmap.md step 3.11 for "the lease samples the directory's size on the watchdog
      tick... over quota it terminates the child."
    - .claude/codingrules.md section 8.7 for "Real Cells are borrowed" and the left-as-found rule
      this quota exists to protect.
    - hivemind.cell.local.session for the watchdog loop that reads these names.
    - hivemind.cell.errors for ScratchQuotaExceededError, the error a breach raises.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# How often the watchdog re-samples a running command's scratch usage and elapsed time. Short
# enough that a fast-filling command is caught before it does much damage; long enough that the
# sampling walk itself is never a meaningful fraction of a command's own runtime.
QUOTA_SAMPLE_INTERVAL_S = 0.25

__all__ = ["QUOTA_SAMPLE_INTERVAL_S", "DirectorySizer", "ScratchQuota", "directory_size_bytes"]

# The shape a scratch-directory measurement takes: given a directory, return its size in bytes.
# `directory_size_bytes` below is the real one; a test substitutes a controllable fake through
# `ScratchQuota.sizer` instead of monkeypatching the module function everyone else also calls.
DirectorySizer = Callable[[Path], int]


def directory_size_bytes(root: Path) -> int:
    """Sum the size of every regular file under `root`, without following symlinks.

    Args:
        root: The directory to measure; typically a lease's own scratch subdirectory.

    Returns:
        The total size in bytes of every regular file found, recursively. A directory that does
        not exist (or stops existing partway through the walk) contributes 0 for the part that
        vanished, rather than raising: the watchdog samples while a command may be mid-cleanup.
    """
    total = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                # SAFETY: `follow_symlinks=False` on both checks below is the whole point of this
                # function -- a symlink inside scratch pointing outside it must never let a lease
                # under- or over-report what it actually holds.
                if entry.is_dir(follow_symlinks=False):
                    total += directory_size_bytes(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    except FileNotFoundError:
        # The directory (or one of its children) was removed between the watchdog's previous
        # tick and this call; nothing left there weighs anything.
        return total
    return total


@dataclass(frozen=True, slots=True)
class ScratchQuota:
    """The byte cap a lease's scratch directory must stay under while a command runs."""

    quota_bytes: int  # `[hive_stand] scratch_quota_mb` converted to bytes, once, by the source.
    # Defaults to the real, symlink-safe walk; a test overrides this to control deterministically
    # when a command's usage is reported as crossing `quota_bytes`, rather than racing a real
    # subprocess's write speed against the watchdog's own poll interval.
    sizer: DirectorySizer = directory_size_bytes
