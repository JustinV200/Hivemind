"""Run a Warden's watching on its tick: sample when due, Patrol when due, only while it is idle.

`watch_tick` is the one call `hivemind.wardens.warden` makes for watch mode, at the end of every
tick, after the state has settled. It does nothing unless the Warden was built with `WatchDeps`
and is in WATCH with no sub-bee and no assignment waiting for its grant: watching is what a
Warden does instead of sitting idle, so it is never done in ACTIVE, and the moment a bee is
assigned it stops until the bee is gone (the schedule is kept, so it resumes where it was, with at
most one sample due at once and not a burst). When the Warden has just come up, the first tick
reads the trail for its last Patrol, so a restart does not review an hour it already reviewed. A
sample is due every `interval_s`; a Patrol every `patrol_interval_s`, whatever its outcome (a
SKIPPED Patrol is retried at the next interval, its observations kept).

Both run inline, as the Warden's awake episodes do, so they are bounded by named timeouts
(`observer.OBSERVE_TIMEOUT_S`, `patrol.PATROL_TIMEOUT_S`) and a tick that watches wakes no earlier
than its Warden's heartbeat cadence. Watching is best-effort: whatever the Hive raises on purpose
while it looks (a device that is away, a store that will not take a sample, a model that is down)
is logged by type and the tick goes on, because a Warden's duties to the Queen outrank its
curiosity about a machine, and a Warden has no recoverable errors (its run loop ends on any).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Called by `hivemind.wardens.warden.Warden`'s tick. Reads and writes the
    Warden's private state directly, as `hivemind.wardens.ticks` does (codingrules 8.5). Calls
    into the sibling observer, patrol, bridge and deps modules.

Key invariants:
    - Nothing runs unless the Warden is WATCH, has no sub-bee and no parked assignment, its Cell
      link is not lost, and its Guard set holds `watch:<its Cell>` (the operator may deny it).
    - Only `WATCH_FAILURES` are caught, and each is logged by its type, never its message.
    - The schedule moves forward after every attempt, so a failing probe is not hammered.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for watch mode.
    - hivemind.wardens.ticks.lease.release_idle_lease, whose definition of idle this follows.
    - hivemind.wardens.watch.deps for the schedule this reads and writes.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError

from hivemind.cell import Cell
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.guard import Capability, CapabilityFamily
from hivemind.memory import MemoryContext
from hivemind.wardens.state import WardenState
from hivemind.wardens.watch.bridge import WardenPatrolPort, last_patrols
from hivemind.wardens.watch.deps import WatchDeps
from hivemind.wardens.watch.observer import WatchObserver
from hivemind.wardens.watch.patrol import Patrol
from waggle.errors import WaggleError

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

# Everything the Hive raises on purpose that watching may meet. A bug (a TypeError, a KeyError)
# is not in the list, so it still ends the loop loudly instead of being logged away.
WATCH_FAILURES: tuple[type[Exception], ...] = (
    HiveMindError,
    WaggleError,
    ValidationError,
    OSError,
    TimeoutError,
)

log = get_logger(__name__)

__all__ = ["WATCH_FAILURES", "watch_tick"]


async def watch_tick(warden: Warden) -> None:
    """Take a sample and run a Patrol if either is due, when this Warden is idle in WATCH.

    Args:
        warden: The owning Warden (read directly; see the module docstring).
    """
    watch = warden._deps.watch
    if watch is None or not _is_idle(warden):
        return
    try:
        await _watch(warden, watch)
    except WATCH_FAILURES as error:
        # By type only: the message of a store or a model error could carry what a machine said.
        log.warning("warden.watch_error", error=type(error).__name__)


def _is_idle(warden: Warden) -> bool:
    """Return whether the Warden has nothing to supervise: WATCH, no sub-bee, nothing parked."""
    return (
        warden._state is WardenState.WATCH
        and not warden._sub_bees
        and not warden._pending
        and not warden._cell_lost  # While the link is down there is nothing to ask, and no lease.
    )


async def _watch(warden: Warden, watch: WatchDeps) -> None:
    """Run what is due: a sample, then a Patrol, advancing the schedule after each."""
    cell = await _watched_cell(warden)
    if cell is None:
        return  # A Warden with no Cell has nothing to watch (its lease was refused outright).
    if not warden._ceiling.allows(Capability(family=CapabilityFamily.WATCH, scope=str(cell.id))):
        return  # The operator's `[guard]` deny list switched watch mode off for this Cell.
    if watch.bind_session is not None:
        watch.bind_session(warden._session)
    deps, schedule, settings = warden._deps, watch.schedule, watch.settings
    if not schedule.is_seeded:
        await _seed(warden, watch)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    owner = str(warden._warden_id)
    if schedule.sample_is_due(deps.clock.now()):
        try:
            await WatchObserver(watch, ctx, owner).sample(cell.access_level)
        finally:
            # Advanced even when it raised, so a failing look is retried at the next interval
            # and not at every tick.
            schedule.next_sample_at = deps.clock.now() + timedelta(seconds=settings.interval_s)
    if schedule.patrol_is_due(deps.clock.now()):
        try:
            await Patrol(watch, ctx, WardenPatrolPort(warden, cell), owner).run()
        finally:
            schedule.next_patrol_at = deps.clock.now() + timedelta(
                seconds=settings.patrol_interval_s
            )


async def _watched_cell(warden: Warden) -> Cell | None:
    """Return the Cell this Warden watches: the one it leased, else the first its source offers."""
    if warden._cell is not None:
        return warden._cell
    # A device's Warden gives its lease back while idle and may never have held one: the source
    # still says which Cell is its own. Listing Cells provisions nothing (Wardens never do).
    cells = await warden._deps.source.cells()
    return cells[0] if cells else None


async def _seed(warden: Warden, watch: WatchDeps) -> None:
    """Read the trail for the last Patrol, so a restarted Warden carries on from where it was."""
    last_any, reviewed = await last_patrols(warden)
    schedule = watch.schedule
    schedule.patrolled_until = reviewed
    anchor = last_any if last_any is not None else warden._deps.clock.now()
    schedule.next_patrol_at = anchor + timedelta(seconds=watch.settings.patrol_interval_s)
    schedule.is_seeded = True
