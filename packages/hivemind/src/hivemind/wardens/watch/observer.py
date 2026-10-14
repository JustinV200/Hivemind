"""Take one read-only observation of the Cell a Warden watches, and keep it as C2 Bee Bread.

A Warden (the per-Cell supervisor) with no bees working watches its Cell instead of sitting idle
(ADR-0054). `WatchObserver.sample` is one such look: it asks its `WatchProbe` for the kinds of
observation the Cell's access level permits (`hivemind.guard.watch_permits`: the process list,
resource use, and logs and file changes under the operator's roots; never the screen, never
input), and keeps the answer for the retention window (`hivemind.wardens.watch.store`). It writes
nothing to the device and holds no lease: what looks is the probe, and a probe is read-only by
construction. A look that fails (the device is away, a command timed out, the store would not take
the sample) is not an error of the Warden's: it is logged by type, never by content, and the next
look tries again, because a Warden must go on watching and heartbeating whatever one device does.
A look that found nothing at all (no session on the Cell, a platform the probe cannot read) is not
kept, so an unwatchable Cell costs no storage and a Patrol is never asked to review emptiness.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Called by `hivemind.wardens.watch.tick`. Calls into the probe protocol,
    `hivemind.wardens.watch.store`, `hivemind.guard.watch` and waggle's swarm messages only.

Key invariants:
    - Only kinds `watch_permits` allows are ever asked for, and logs and file changes only when
      the operator named roots.
    - Every probe call has a timeout, and only the probe's declared failures are caught.
    - Nothing the machine said is logged or put on the trail here.
    - The file-change cursor never moves past the moment of the sample, so a file stamped in the
      future cannot hide later changes.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for watch mode.
    - hivemind.wardens.watch.probe for the probes this calls.
    - hivemind.wardens.watch.store for where the samples go.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from pydantic import ValidationError

from hivemind.cell import AccessLevel
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.guard.watch import WatchObservation, watch_permits
from hivemind.memory import MemoryContext
from hivemind.wardens.watch import store
from hivemind.wardens.watch.deps import WatchDeps
from hivemind.wardens.watch.probe.protocol import PROBE_FAILURES
from waggle.messages.swarm import Observation, ObservationKind

OBSERVE_TIMEOUT_S = 90.0  # Seconds one look may take: a packet's reply (60) or two commands (30).

# The same four things under the Guard's names; the Guard is the only one that decides which.
_GUARD_NAME = {
    ObservationKind.PROCESS_LIST: WatchObservation.PROCESS_LIST,
    ObservationKind.RESOURCE_USE: WatchObservation.RESOURCE_USE,
    ObservationKind.LOGS: WatchObservation.LOGS,
    ObservationKind.FILE_CHANGES: WatchObservation.FILE_CHANGES,
}
_FILE_KINDS = frozenset({ObservationKind.LOGS, ObservationKind.FILE_CHANGES})  # They need roots.
_KEEP_FAILURES = (HiveMindError, ValidationError, OSError)  # What the store may raise on purpose.

log = get_logger(__name__)

__all__ = ["OBSERVE_TIMEOUT_S", "WatchObserver", "is_empty", "permitted_kinds"]


def permitted_kinds(level: AccessLevel, has_roots: bool) -> tuple[ObservationKind, ...]:
    """Return the kinds of observation watch mode may make on a Cell at `level`.

    Args:
        level: The Cell's access level.
        has_roots: Whether the operator named directories to read logs and changes from; without
            them those two kinds have nowhere to look and are not asked for.

    Returns:
        The kinds, in the wire enum's order; none of them is the screen or the input.
    """
    return tuple(
        kind
        for kind in ObservationKind
        if watch_permits(level, _GUARD_NAME[kind]) and (has_roots or kind not in _FILE_KINDS)
    )


def is_empty(observation: Observation) -> bool:
    """Return whether an Observation holds nothing at all: no process, resource, line or change."""
    return not (
        observation.processes
        or observation.resources
        or observation.log_lines
        or observation.file_changes
    )


class WatchObserver:
    """Takes a watching Warden's samples; owns no state, the schedule belongs to `WatchDeps`."""

    def __init__(self, watch: WatchDeps, ctx: MemoryContext) -> None:
        """Bind the observer to one Warden's watch settings and memory.

        Args:
            watch: The probe, the settings and the schedule (whose file cursor this moves).
            ctx: The Warden's store, identity and clock, which the sample is kept with.
        """
        self._watch = watch
        self._ctx = ctx

    async def sample(self, level: AccessLevel) -> bool:
        """Take one observation now and keep it.

        Args:
            level: The access level of the Cell being watched.

        Returns:
            True when a sample was kept; False when the look failed or found nothing.
        """
        settings = self._watch.settings
        kinds = permitted_kinds(level, bool(settings.roots))
        if not kinds:
            return False
        reads_files = any(kind in _FILE_KINDS for kind in kinds)
        cursor = self._watch.schedule.file_cursor
        since_at = cursor if ObservationKind.FILE_CHANGES in kinds else None
        try:
            async with asyncio.timeout(OBSERVE_TIMEOUT_S):
                observation = await self._watch.probe.observe(
                    kinds, settings.roots if reads_files else (), since_at
                )
        except PROBE_FAILURES as error:
            # By type only: the message of a probe's error could carry a path the machine named.
            log.warning("warden.watch_failed", error=type(error).__name__)
            return False
        if is_empty(observation):
            log.debug("warden.watch_empty", reason=observation.reason)
            return False
        try:
            await store.save(observation, self._ctx, settings.retention_s)
        except _KEEP_FAILURES as error:
            log.warning("warden.watch_not_kept", error=type(error).__name__)
            return False
        self._watch.schedule.file_cursor = _next_cursor(observation, cursor)
        return True


def _next_cursor(observation: Observation, current: datetime | None) -> datetime | None:
    """Return the file-change cursor after `observation`: its newest change, never in the future."""
    if not observation.file_changes:
        return current
    # Changes arrive oldest first, so the last is the newest; a modification time can be set to
    # anything, and a cursor past the present would hide every real change that follows.
    candidate = min(observation.file_changes[-1].at, observation.observed_at)
    return candidate if current is None else max(current, candidate)
