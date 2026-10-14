"""Define WatchDeps: what a Warden that watches its idle Cell is built with, and when it next looks.

A Warden (the per-Cell supervisor) with no bees working watches its Cell instead of sitting idle
(ADR-0054): every `interval_s` it takes one read-only observation and keeps it for `retention_s`,
and every `patrol_interval_s` it reviews what it kept (the Patrol). `WardenDeps` carries all of
this as one optional field, because that class is at codingrules 5.1's length limit, and a Warden
with no `WatchDeps` (a Virtual Cell's, or one whose composition root does not watch) never looks at
anything. `WatchSettings` holds the numbers from `[supervision.watch]`; `WatchProbe` is how this
Warden looks (its own session on the Hive Stand, or a Pollen Packet on a device); `WatchSchedule`
is the one mutable part, the instants the next sample and the next Patrol are due and where the
last of each left off. It lives inside the frozen deps rather than on the Warden because
`Warden.__init__` and its class are at the same limit, and the schedule belongs to this feature
alone, the way `hivemind.wardens.ticks.honey.HoneyRelay` belongs to its.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Imported by `hivemind.wardens.deps` (so its import closure stays light: no
    Warden, no model machinery) and built by the composition roots (`hivemind.cli.compose`). Read
    and written by `hivemind.wardens.watch.tick`. Calls into the probe protocol and the standard
    library only.

Key invariants:
    - `WatchDeps` is frozen; only `WatchSchedule`, its one collaborator that changes, is mutable,
      and only `hivemind.wardens.watch.tick` and `.patrol` write it (codingrules 8.5).
    - `bind_session` is None for a probe that looks through something other than the Warden's own
      session; the Warden never inspects the probe's type to decide (codingrules 8.7).
    - A schedule that has never run is due at once: a Warden that has just settled to WATCH takes
      its first sample immediately and its first Patrol one interval later.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for watch mode and the Patrol.
    - hivemind.manifest.schema.supervision.WatchSection for the numbers' source.
    - hivemind.wardens.deps for WardenDeps, which holds the one optional `watch` field.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from hivemind.cell import CellSession
from hivemind.wardens.watch.probe.protocol import WatchProbe

__all__ = ["WatchDeps", "WatchSchedule", "WatchSettings"]


@dataclass(frozen=True, slots=True)
class WatchSettings:
    """The numbers a watching Warden runs by, from the manifest's `[supervision.watch]`.

    Attributes:
        interval_s: Seconds between two observation samples.
        retention_s: Seconds a sample is kept in Bee Bread before it may be purged.
        patrol_interval_s: Seconds between two Patrols.
        cpu_share_alarm: A newly seen process using at least this share of the CPU (0 to 1) is
            labelled a fact for the Patrol to weigh.
        disk_fill_alarm: A disk filled at least this fraction (0 to 1) is likewise labelled.
        roots: Absolute directories whose log files and file changes are watched; empty watches
            no files, only processes and resource use.
    """

    interval_s: float
    retention_s: float
    patrol_interval_s: float
    cpu_share_alarm: float
    disk_fill_alarm: float
    roots: tuple[str, ...] = ()


@dataclass(slots=True)
class WatchSchedule:
    """When the next sample and Patrol are due, and where the last of each left off.

    Owns mutable state (codingrules 8.5, documented): every field changes as this Warden watches.
    """

    next_sample_at: datetime | None = None  # None: never sampled, so due at once.
    next_patrol_at: datetime | None = None  # None: not yet seeded, so set on the first tick.
    patrolled_until: datetime | None = (
        None  # When the newest observation a Patrol reviewed was kept.
    )
    reviewed_ids: frozenset[str] = frozenset()  # Those kept at that very instant, already reviewed.
    file_cursor: datetime | None = None  # The newest file change seen, for the next sample.
    is_seeded: bool = False  # Whether the first tick has read the trail for the last Patrol.

    def sample_is_due(self, now: datetime) -> bool:
        """Return whether a sample is due at `now`."""
        return self.next_sample_at is None or now >= self.next_sample_at

    def patrol_is_due(self, now: datetime) -> bool:
        """Return whether a Patrol is due at `now`."""
        return self.next_patrol_at is not None and now >= self.next_patrol_at


@dataclass(frozen=True, slots=True)
class WatchDeps:
    """Everything a Warden needs to watch its idle Cell.

    Attributes:
        probe: How this Warden looks at its Cell, read-only.
        settings: The numbers from `[supervision.watch]`.
        schedule: When it looks next; shared by every tick of this one Warden.
        bind_session: Lends the Warden's own session to the probe, or takes it back with None.
            Set for a probe that looks through that session (the Hive Stand's), None for one that
            does not (a Level 0 device's, which asks its packet).
    """

    probe: WatchProbe
    settings: WatchSettings
    schedule: WatchSchedule = field(default_factory=WatchSchedule)
    bind_session: Callable[[CellSession | None], None] | None = None
