"""Define what watch mode may observe on a Real Cell, as data: the bound `watch:<node>` grants.

Watch mode (a Real Cell's Warden observing its device with no active bees, roadmap 11.10) is how
the Hive learns a borrowed machine's habits without acting on it. Roadmap 10.7 fixes its bound
here, before the watcher exists: a `watch:<node>` capability admits only the observations the
`READ_ONLY` access level would allow on that device, whatever level its lease holds, and never
the screen or the input. Those two are a separate grant (`exoskeleton:real_display`, checked at
its own enforcement point) that is never issued implicitly, because watching a person's display
or keystrokes is a different act from reading a process list. Logs and file-change events are
observed only under the roots the watcher is allowed to read (its `fs:read` scopes), which the
watcher checks per event.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by the watch-mode Warden of
    roadmap 11.10 and by the Observation Hive's Cell view. Calls into `hivemind.cell.tiers` only.

Key invariants:
    - `watch_permits` is False for SCREEN_CAPTURE and INPUT_CAPTURE at every level.
    - A higher access level never widens what watch mode observes: every level maps to the
      `READ_ONLY` set.

See Also:
    - .claude/roadmap.md steps 10.7 and 11.10 for the bound and the watcher.
    - hivemind.guard.access for what each level permits a bee that acts.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType

from hivemind.cell.tiers import AccessLevel

__all__ = ["WATCH_OBSERVATIONS", "WatchObservation", "watch_permits"]


class WatchObservation(Enum):
    """One kind of thing a watcher could observe on a device."""

    PROCESS_LIST = "process_list"  # Which processes run, their names and resource use.
    RESOURCE_USE = "resource_use"  # CPU, memory, disk and network totals.
    LOGS = "logs"  # Log lines, only under the roots the watcher may read.
    FILE_CHANGES = "file_changes"  # Create, modify and delete events, only under those roots.
    SCREEN_CAPTURE = "screen_capture"  # Never watch mode: a separate, explicit grant.
    INPUT_CAPTURE = "input_capture"  # Never watch mode: a separate, explicit grant.


# What READ_ONLY allows a watcher to see; the bound for every level (module docstring).
_READ_ONLY_OBSERVATIONS = frozenset(
    {
        WatchObservation.PROCESS_LIST,
        WatchObservation.RESOURCE_USE,
        WatchObservation.LOGS,
        WatchObservation.FILE_CHANGES,
    }
)

# Every level, however wide, watches exactly what READ_ONLY would: acting is not watching.
WATCH_OBSERVATIONS: Mapping[AccessLevel, frozenset[WatchObservation]] = MappingProxyType(
    dict.fromkeys(AccessLevel, _READ_ONLY_OBSERVATIONS)
)


def watch_permits(level: AccessLevel, observation: WatchObservation) -> bool:
    """Decide whether watch mode on a Cell at `level` may make `observation`.

    Args:
        level: The Cell's access level, as stored with its lease.
        observation: What the watcher wants to observe.

    Returns:
        True for the four `READ_ONLY` observations at every level; False for screen and input
        capture, always.
    """
    return observation in WATCH_OBSERVATIONS[level]
