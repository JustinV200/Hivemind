"""Define SweepSchedule: a pure, timer-shaped decision for when a House Bee sweep is next due.

A House Bee (a maintenance role, `hivemind.workers.roles.house_bee.HouseBee`) runs its sweep
(demotion, then compaction) as routine upkeep rather than an emergency response (codingrules
section 8.9: "demotion is a duty, not an emergency"). `SweepSchedule` is the pure timer a
supervisor (a Warden or the Queen; wiring one is a later, parallel dispatch's job -- see this
module's own "Fits into the Hive") checks on its own tick loop: given when the last sweep finished
and the current time, it says when the next one is due. Keeping this pure (no clock of its own, no
I/O) is what makes it independently testable with a `FakeClock`-derived timestamp, the same reason
`hivemind.memory.relevance.score` and `hivemind.memory.demote.should_demote` take `now` as a
parameter instead of reading a clock.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.house_bee`. Built by whichever
    composition root reads the manifest's `[memory] sweep_interval_s`
    (`hivemind.manifest.schema.supervision.MemorySection.sweep_interval_s`) and wires a House Bee
    onto a timer -- explicitly not this dispatch's job (`queen/` and `wardens/` belong to a
    parallel dispatch); this module only defines the pure decision that timer will call. Calls into
    the standard library only.

Key invariants:
    - `next_due`/`is_due` are pure: given the same `last_run` and `now`, they always return the
      same answer, and neither performs any I/O or reads a clock of its own.
    - `next_due(None, now)` is always `now`: a House Bee that has never swept is due immediately,
      so a fresh Hive's first tick runs one sweep right away rather than waiting a full interval.

See Also:
    - .claude/codingrules.md section 8.9 for "demotion is a duty, not an emergency", the same
      framing this schedule extends to compaction.
    - .claude/roadmap.md step 4.3 for `next_due`'s signature verbatim.
    - hivemind.manifest.schema.supervision for `MemorySection.sweep_interval_s`, the manifest field
      a composition root reads `interval_s` from.
    - hivemind.workers.roles.house_bee.sweep for run_sweep, the work this schedule times.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["SweepSchedule"]


@dataclass(frozen=True, slots=True)
class SweepSchedule:
    """A pure timer: when a House Bee sweep last ran plus `interval_s` says when the next is due.

    Attributes:
        interval_s: Seconds between one sweep and the next; a composition root reads this from
            `hivemind.manifest.schema.supervision.MemorySection.sweep_interval_s`.
    """

    interval_s: float

    def next_due(self, last_run: datetime | None, now: datetime) -> datetime:
        """Return when the next sweep should run.

        Args:
            last_run: When the previous sweep finished, or `None` when none has run yet.
            now: The reference time (a `FakeClock`-derived timestamp in tests).

        Returns:
            `now` when `last_run` is `None` (module docstring's "Key invariants"); otherwise
            `last_run` plus `interval_s` seconds.
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
