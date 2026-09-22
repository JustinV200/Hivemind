"""Define UndertakerSweepSchedule: a pure, timer-shaped decision for when the next sweep is due.

Mirrors `hivemind.workers.roles.house_bee.schedule.SweepSchedule` almost exactly, for the same
reason: `hivemind.workers.roles.undertaker.sweep.sweep_orphans` runs once on Queen startup
(roadmap step 5.8), but a long-lived Queen also needs to re-sweep periodically -- an orphaned lease
or a dormant Cell past its own `dormant_until` can appear at any point during a run, not only at
boot. No manifest field exists yet for this cadence (this dispatch may not edit manifest files;
`hivemind.manifest.schema.placement.VirtualCellsOverwinterSection` covers the pool's own bounds,
not a sweep interval), so `DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S` is a plain module constant until a
future step wires a `[virtual_cells]`-independent manifest field to it.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.undertaker`. Built by whichever
    composition root wires the Undertaker's periodic sweep onto the Queen's own tick (mirroring
    `hivemind.queen.ticks.housekeeping.run_housekeeping`'s own use of `SweepSchedule`) -- not this
    dispatch's job (`queen/` belongs to a concurrent dispatch); this module only defines the pure
    decision that tick will call. Calls into the standard library only.

Key invariants:
    - `next_due`/`is_due` are pure: same `last_run`/`now` in, same answer out, no I/O, no clock of
      its own (mirrors `SweepSchedule`'s own invariant).
    - `next_due(None, now)` is always `now`: a Hive that has never swept sweeps at once, matching
      `SweepSchedule`'s own first-run rule.

See Also:
    - .claude/roadmap.md step 5.8 for "on Queen startup sweeps orphans", the event this schedule
      generalises to "and periodically after that".
    - hivemind.workers.roles.house_bee.schedule for SweepSchedule, the pattern this module mirrors.
    - hivemind.workers.roles.undertaker.sweep for sweep_orphans, the work this schedule times.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Five minutes: frequent enough that an orphan does not linger for long, cheap enough that it never
# competes meaningfully with a Queen tick's other work. Independent of the manifest until a future
# step adds a [virtual_cells]-independent field for it (module docstring).
DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S = 300.0

__all__ = ["DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S", "UndertakerSweepSchedule"]


@dataclass(frozen=True, slots=True)
class UndertakerSweepSchedule:
    """A pure timer: when the Undertaker's orphan sweep last ran plus `interval_s` says when next.

    Attributes:
        interval_s: Seconds between one sweep and the next; `DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S`
            until a manifest field exists for it.
    """

    interval_s: float = DEFAULT_UNDERTAKER_SWEEP_INTERVAL_S

    def next_due(self, last_run: datetime | None, now: datetime) -> datetime:
        """Return when the next sweep should run.

        Args:
            last_run: When the previous sweep finished, or `None` when none has run yet.
            now: The reference time (a `FakeClock`-derived timestamp in tests).

        Returns:
            `now` when `last_run` is `None`; otherwise `last_run` plus `interval_s` seconds.
        """
        if last_run is None:
            return now
        return last_run + timedelta(seconds=self.interval_s)

    def is_due(self, last_run: datetime | None, now: datetime) -> bool:
        """Return whether a sweep should run now: `next_due(last_run, now) <= now`.

        Args:
            last_run: When the previous sweep finished, or `None` when none has run yet.
            now: The reference time to check against.

        Returns:
            `True` when a sweep is due, `False` otherwise.
        """
        return self.next_due(last_run, now) <= now
