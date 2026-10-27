"""Write the Queen's own periodic Handoff, so a restart reads only a bounded stretch of trail.

Roadmap 13.2 (ADR-0062): a bee writes a Handoff so the next episode resumes without its whole
history; the Queen's is a `QueenSnapshot`. Every `[queen] snapshot_interval_s` her tick folds the
Hive Stand's lease events since the last snapshot's cursor into the leases still open
(`hivemind.queen.requeening.leases.fold_leases`) and writes the result with a new cursor, and
prunes the handled-envelope rows the replay window no longer needs. Recovery then folds only the
events since the newest cursor (`current_leases`), however old the Hive is. The cursor trails the
snapshot by `CURSOR_MARGIN_S`: a lease event whose write lands a moment late is still folded next
time, and folding an event twice changes nothing.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    requeening sub-package. `take_snapshot_when_due` is called once per tick by
    `hivemind.queen.ticks.housekeeping`; `take_snapshot` and `current_leases` by `.recover`.
    Calls into `hivemind.cell` (hive_stand_cell_id), `.leases`, `.models`, `.store` and waggle
    only.

Key invariants:
    - No snapshot is written while recovery is still settling: the first one of a Queen's life
      follows `queen.requeened`, from the fold recovery read.
    - A snapshot's cursor never moves backwards.
    - A snapshot is the Queen's own bookkeeping and records no trail event (ADR-0062).

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md, "The Queen's own Handoff (13.2)".
    - hivemind.queen.requeening.leases for the fold.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import TYPE_CHECKING

from hivemind.cell import hive_stand_cell_id
from hivemind.queen.requeening.leases import fold_leases, lease_events
from hivemind.queen.requeening.models import MAX_SNAPSHOT_WARDENS, OpenLease, QueenSnapshot
from waggle.transport import REPLAY_WINDOW_S

if TYPE_CHECKING:
    # Types only: `hivemind.queen.deps` imports this sub-package (`QueenDeps.requeening`).
    from hivemind.queen.deps import QueenDeps, WardenLink

CURSOR_MARGIN_S = 60.0  # How far the cursor trails the snapshot: far beyond any write's delay.

__all__ = ["CURSOR_MARGIN_S", "current_leases", "take_snapshot", "take_snapshot_when_due"]


async def take_snapshot_when_due(
    deps: QueenDeps, wardens: Sequence[WardenLink]
) -> QueenSnapshot | None:
    """Write a snapshot when the interval has passed since her last; once per tick.

    Args:
        deps: The Queen's collaborators; `requeening` is read and written.
        wardens: Every Warden attached now.

    Returns:
        The snapshot written, or None when none was due (or recovery is still settling).
    """
    book = deps.requeening
    if book.pending is not None:
        return None  # Recovery writes the first one when it settles (module invariant).
    last = book.last_snapshot_at
    if last is not None and deps.clock.now() - last < timedelta(seconds=book.snapshot_interval_s):
        return None  # Not due yet; the next tick checks again.
    return await take_snapshot(deps, wardens)


async def take_snapshot(deps: QueenDeps, wardens: Sequence[WardenLink]) -> QueenSnapshot:
    """Fold the lease events since the last snapshot, write the result, and prune old rows.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden attached now, named in the snapshot.

    Returns:
        The snapshot written.
    """
    book, now = deps.requeening, deps.clock.now()
    previous = await book.store.latest_snapshot()
    margin = now - timedelta(seconds=CURSOR_MARGIN_S)
    cursor = margin if previous is None else max(previous.cursor, margin)
    snapshot = QueenSnapshot(
        taken_at=now,
        cursor=cursor,
        open_leases=await current_leases(deps, previous),
        wardens=tuple(sorted(link.warden_id for link in wardens))[:MAX_SNAPSHOT_WARDENS],
    )
    # Latency: two local SQLite writes; the trail reads above were bounded by the old cursor.
    await book.store.put_snapshot(snapshot)
    await book.store.prune_handled(now - timedelta(seconds=REPLAY_WINDOW_S))
    book.last_snapshot_at = now
    return snapshot


async def current_leases(deps: QueenDeps, previous: QueenSnapshot | None) -> tuple[OpenLease, ...]:
    """Return the Hive Stand leases open now: `previous`'s, folded with every event since.

    Args:
        deps: The Queen's collaborators; the trail is read.
        previous: The newest snapshot, or None to fold the whole trail (a Hive's first start).

    Returns:
        The leases open now, oldest first.
    """
    cell_id = hive_stand_cell_id(deps.identity.node_id)
    since = previous.cursor if previous is not None else None
    start = previous.open_leases if previous is not None else ()
    return fold_leases(start, await lease_events(deps, cell_id, since))
