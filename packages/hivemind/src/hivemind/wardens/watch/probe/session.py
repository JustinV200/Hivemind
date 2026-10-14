"""Provide SessionWatchProbe: look at the Warden's own Cell through the Warden's own session.

A Warden (the per-Cell supervisor) with no bees working watches its Cell (a unit of compute) instead
of sitting idle (ADR-0054). Where the Warden runs beside the Cell, as the Hive Stand's does, the
only way anything runs a command on a Cell is its `CellSession` (codingrules section 4), so this
probe looks through that session: one fixed script that reads kernel tables, free space and the
file listings under the operator's roots, and one more, fixed too, that reads the end of the
newest log files among them (`hivemind.wardens.watch.probe.commands`). What comes back is parsed
into the same `Observation` a Pollen Packet sends (`hivemind.wardens.watch.probe.system` and
`.files`), so nothing downstream knows which probe it is talking to. The probe is read-only by
construction: it has no write method of the session in reach of any code path, no command text from
a request, and it never opens or holds a lease; the session is lent to it by whoever owns the
Warden's lease, and it looks at nothing while there is none.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch.probe sub-package. Built by the Hive Stand's composition root and given the Warden's
    session by the watch glue (`hivemind.wardens.watch.tick`); used through `WatchProbe` by
    `hivemind.wardens.watch.observer`. Calls into `hivemind.cell`, the probe's sibling modules and
    waggle only.

Key invariants:
    - Only `exec` of the two fixed scripts is ever called on the session; never `put_file`,
      `delete_file`, `start` or `stop`.
    - At most two commands run per observation (the lease records the process id of every command
      it runs and kills them all when it is released, so a probe must not run many).
    - No session, a closed one or a platform without collectors gives an Observation with nothing
      in it and a reason; it is not an error.
    - Everything in the Observation describes the operator's machine (`C2`); nothing here logs it.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for watch mode.
    - hivemind.wardens.watch.probe.protocol for WatchProbe, the contract.
    - hivemind.swarm.watch for PollenWatchProbe, which asks a packet instead of running commands.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from hivemind.cell import CellSession, CommandTimeoutError, ExecSpec, run
from hivemind.wardens.watch.probe.commands import (
    EXEC_ENV,
    MARK_PREFIX,
    SYSTEM_TIMEOUT_S,
    TAIL_TIMEOUT_S,
    sections_for,
    system_argv,
    tail_argv,
)
from hivemind.wardens.watch.probe.files import (
    ChangeTracker,
    newest_logs,
    parse_roots,
    parse_tail,
)
from hivemind.wardens.watch.probe.system import (
    CpuBaseline,
    parse_kernel,
    parse_processes,
    parse_resources,
    process_samples,
    split_sections,
)
from waggle.clock import Clock
from waggle.ids import DeviceId
from waggle.messages.swarm import (
    FileChange,
    LogLine,
    Observation,
    ObservationKind,
    ProcessSample,
    ResourceSample,
)
from waggle.messages.swarm.watch import (
    DEFAULT_OBSERVE_ITEMS,
    MAX_OBSERVE_ITEMS,
    MAX_OBSERVE_ROOTS,
)

MARK_BYTES = 8  # Random bytes in each run's mark: 64 bits nothing on the machine can guess.
_FILE_KINDS = (ObservationKind.LOGS, ObservationKind.FILE_CHANGES)  # The kinds that read roots.

__all__ = ["SessionWatchProbe"]


@dataclass(frozen=True, slots=True)
class _Plan:
    """What one observation asks for, after the roots are vetted."""

    kinds: tuple[ObservationKind, ...]
    roots: tuple[str, ...]
    since_at: datetime | None


@dataclass(slots=True)
class _Parts:
    """What one observation collected so far, and what it could not."""

    processes: tuple[ProcessSample, ...] = ()
    resources: ResourceSample | None = None
    log_lines: tuple[LogLine, ...] = ()
    file_changes: tuple[FileChange, ...] = ()
    is_truncated: bool = False
    missing: list[str] = field(default_factory=list)  # Kinds asked for and not collected.


class SessionWatchProbe:
    """Observe the Warden's own Cell through the Warden's own session; read-only by construction.

    Owns mutable state (codingrules 8.5, documented): the session it was lent, the previous
    sample's CPU ticks (to measure a share over the interval) and each root's last file listing (to
    tell a created file from a modified one and to see deletions).
    """

    def __init__(
        self,
        device_id: DeviceId,
        clock: Clock,
        platform: str = sys.platform,
        max_items: int = DEFAULT_OBSERVE_ITEMS,
    ) -> None:
        """Build the probe for one Cell.

        Args:
            device_id: The id the Observation carries; for the Hive Stand's own Cell one derived
                from the Cell's id, since there is no device.
            clock: Stamps each observation.
            platform: The platform the session's commands run on (`sys.platform` for a local
                session); anything but Linux has no collectors and observes nothing.
            max_items: The most items to report in each of the process list, the file changes and
                the log lines.
        """
        self._device_id = device_id
        self._clock = clock
        self._platform = platform
        self._max_items = min(max_items, MAX_OBSERVE_ITEMS)
        self._session: CellSession | None = None
        self._baseline = CpuBaseline()
        self._tracker = ChangeTracker()

    def use_session(self, session: CellSession | None) -> None:
        """Lend the probe the Warden's session, or take it back with None.

        Args:
            session: The session the Warden's lease opened; None once the lease is gone.
        """
        self._session = session

    async def observe(
        self, kinds: Sequence[ObservationKind], roots: Sequence[str], since_at: datetime | None
    ) -> Observation:
        """Take one observation of the Cell.

        Args:
            kinds: What to report; the caller has already narrowed them to what the Cell's access
                level allows.
            roots: Absolute directories to read logs from and watch for file changes; one that is
                not absolute is ignored.
            since_at: The file-change cursor, or None for the probe's own recent window.

        Returns:
            The Observation; a kind that could not be collected is empty and named in `reason`.

        Raises:
            SessionClosedError: The session closed during the observation.
            CommandTimeoutError: The first command did not finish within `SYSTEM_TIMEOUT_S`.
        """
        session = self._session
        if session is None or not session.is_open:
            return self._observation(_Parts(), "The Warden has no open session on its Cell.")
        if not self._platform.startswith("linux"):
            return self._observation(_Parts(), f"Platform {self._platform} has no collectors.")
        plan = _Plan(tuple(dict.fromkeys(kinds)), _usable_roots(roots), since_at)
        parts = _Parts()
        mark = _new_mark()
        argv = system_argv(sections_for(plan.kinds), mark, str(session.scratch_dir), plan.roots)
        sections = await _sections(session, argv, SYSTEM_TIMEOUT_S, mark)
        self._system(plan, sections, parts)
        await self._files(session, plan, sections, parts)
        reason = f"Not collected: {', '.join(parts.missing)}." if parts.missing else "Observed."
        return self._observation(parts, reason)

    def _system(self, plan: _Plan, sections: dict[str, str], parts: _Parts) -> None:
        """Fill the process list and the resources from the first command's sections."""
        facts = parse_kernel(sections)
        if ObservationKind.PROCESS_LIST in plan.kinds:
            raw = [] if facts is None else parse_processes(sections.get("procs", ""), facts)
            if facts is None or not raw:
                parts.missing.append(ObservationKind.PROCESS_LIST.value)
            else:
                parts.processes, cut = process_samples(raw, facts, self._baseline, self._max_items)
                parts.is_truncated = parts.is_truncated or cut
        if ObservationKind.RESOURCE_USE in plan.kinds:
            parts.resources = None if facts is None else parse_resources(sections, facts)
            if parts.resources is None:
                parts.missing.append(ObservationKind.RESOURCE_USE.value)

    async def _files(
        self, session: CellSession, plan: _Plan, sections: dict[str, str], parts: _Parts
    ) -> None:
        """Fill the file changes from the listing, and the log lines from a second command."""
        wanted = [kind for kind in _FILE_KINDS if kind in plan.kinds]
        if not wanted:
            return
        if not plan.roots:
            parts.missing.extend(kind.value for kind in wanted)  # Nothing to look inside.
            return
        listings = parse_roots(sections, plan.roots)
        # A root the script never reached was not looked at, so the sample is only a lower bound.
        parts.is_truncated = parts.is_truncated or len(listings) < len(plan.roots)
        parts.is_truncated = parts.is_truncated or any(
            listing.is_truncated for listing in listings.values()
        )
        if ObservationKind.FILE_CHANGES in plan.kinds:
            parts.file_changes, cut = self._tracker.changes(
                listings, plan.since_at, self._clock.now(), self._max_items
            )
            parts.is_truncated = parts.is_truncated or cut
        if ObservationKind.LOGS in plan.kinds:
            logs, cut = newest_logs(listings.values())
            parts.is_truncated = parts.is_truncated or cut
            if logs:
                await self._tail(session, logs, parts)

    async def _tail(self, session: CellSession, logs: list[tuple[str, int]], parts: _Parts) -> None:
        """Read the end of the chosen log files with the second command."""
        mark = _new_mark()
        argv = tail_argv(mark, [path for path, _ in logs])
        try:
            sections = await _sections(session, argv, TAIL_TIMEOUT_S, mark)
        except CommandTimeoutError:
            # The first command's findings are worth keeping; the logs are what was lost.
            parts.missing.append(ObservationKind.LOGS.value)
            return
        parts.log_lines, cut = parse_tail(sections, logs, self._max_items)
        parts.is_truncated = parts.is_truncated or cut

    def _observation(self, parts: _Parts, reason: str) -> Observation:
        """Build the Observation from what was collected."""
        return Observation(
            device_id=self._device_id,
            observed_at=self._clock.now(),
            processes=parts.processes,
            resources=parts.resources,
            log_lines=parts.log_lines,
            file_changes=parts.file_changes,
            is_truncated=parts.is_truncated,
            reason=reason,
        )


def _new_mark() -> str:
    """Return a fresh marker token, unguessable by anything on the machine."""
    return MARK_PREFIX + secrets.token_hex(MARK_BYTES)


def _usable_roots(roots: Sequence[str]) -> tuple[str, ...]:
    """Keep the roots a script can take safely: absolute, free of NUL, once each, at most 16."""
    # An absolute path cannot be read by find or tail as an option, and a NUL cannot be an argument.
    absolute = (root for root in roots if root.startswith("/") and "\0" not in root)
    return tuple(dict.fromkeys(absolute))[:MAX_OBSERVE_ROOTS]


async def _sections(
    session: CellSession, argv: tuple[str, ...], timeout_s: float, mark: str
) -> dict[str, str]:
    """Run one fixed command on the session and split what it printed into sections."""
    done = await run(session, ExecSpec(argv=argv, env=EXEC_ENV, timeout_s=timeout_s))
    return split_sections(done.stdout.decode("utf-8", errors="replace"), mark)
