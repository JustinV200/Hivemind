"""Define OverwinterPool: bookkeeping and selection for dormant Virtual Cells, nothing else.

Roadmap step 5.9 (ADR-0029), reconciled under step 5.6's own dispatch: `hivemind.hive.lifecycle.
CellLifecycle` now owns every Virtual Cell state edge and every `hivemind.hive.backends.base.
CellBackend` call, including pause/resume/destroy for an Overwintered Cell (that module's own
docstring, "Reconcile CellLifecycle and OverwinterPool"). `OverwinterPool` is left with exactly
what neither the state machine nor the backend already covers: which Cell is dormant, since when,
until when, and which one is "the oldest matching entry" for a given image. `admit` scrubs a
released Cell (an injected `Scrubber`, since the pool never opens a session of its own) and
records it dormant, but does not pause it; `claim`/`claim_by_id` pick an entry and remove it, but
do not resume it; `evict_expired` returns the ids past their own `dormant_until`, but does not
destroy them. The lifecycle calls `backend.pause`/`backend.resume`/`backend.destroy` itself,
immediately around each of these calls, so the abstract Virtual Cell state (`hivemind.hive.
cell_state.VirtualCellStatus`) and the backend's own real state never drift apart mid-call the way
they could when two classes each called the backend independently.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.hive.overwinter`.
    Called by `hivemind.hive.lifecycle.CellLifecycle` on `overwinter()` (`admit`), `resume()`
    (`claim_by_id`) and `claim_for_image()` (`claim`), and by `CellLifecycle.evict_expired()`
    (`evict_expired`), which then tears each returned id down itself. Calls into `hivemind.cell`
    (Cell, CombShieldLevel), `hivemind.hive.cell_state` (VirtualCellStatus, assert_dormant_allowed,
    assert_transition), `hivemind.hive.models` (VirtualCellSpec), `hivemind.hive.overwinter.policy`
    (OverwinterConfig, PoolView) and waggle only -- no `hivemind.hive.backends.base.CellBackend` and
    no `hivemind.pheromone` any more: neither a backend call nor a trail write is this module's job
    now (both moved to `hivemind.hive.lifecycle`, this dispatch's own reconciliation).

Key invariants:
    - `admit` never admits a `CombShieldLevel.NIGHT_VEIL` Cell: `cell_state.assert_dormant_allowed`
      raises `InvalidCellTransitionError` before `scrub` is ever awaited (ADR-0029, codingrules
      section 8.7) -- kept here too, as a second guard alongside the one in `CellLifecycle.
      overwinter`, the same "not the only guard" shape the Night Veil rule uses everywhere else.
    - `admit`/`claim`/`claim_by_id`/`evict_expired` each call `hivemind.hive.cell_state.
      assert_transition` for the abstract edge they represent, purely as a consistency guard: the
      table always allows these edges today, so this never fires in practice, but it keeps this
      module honest about which edge each method stands in for if the table ever changes.
    - `dormant_candidates` returns this module's own `PooledCandidate`, never `hivemind.queen.
      placement.inventory.DormantCandidate` directly: `hive` is Layer 3 and `queen` is Layer 6
      (codingrules section 4's layer table). A Layer-6 caller converts one `PooledCandidate` into
      one `DormantCandidate` per row.
    - Every admitted Cell's disk (`VirtualCellSpec.disk_bytes`) and image are what `view()` sums for
      `hivemind.hive.overwinter.policy.PoolView`; `evict_expired`, `claim` and `claim_by_id` all
      remove the entry before returning, so occupancy is never double-counted mid-call.

See Also:
    - docs/adr/0029-overwintering-policy.md for the pool this module implements.
    - hivemind.hive.lifecycle for CellLifecycle, which now owns every backend call and every
      cell.* trail event this pool's own callers used to write directly.
    - hivemind.hive.overwinter.policy for decide_release, OverwinterConfig and PoolView, this
      module's pure counterpart.
    - hivemind.hive.cell_state for the Virtual Cell state machine this module's edges follow.
    - hivemind.workers.roles.undertaker.sweep for sweep_orphans, whose DormantEvictor now points at
      `CellLifecycle.evict_expired`, not this module's own `evict_expired` directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from hivemind.cell import Cell, CombShieldLevel
from hivemind.hive.cell_state import VirtualCellStatus, assert_dormant_allowed, assert_transition
from hivemind.hive.errors import InvalidCellTransitionError
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import OverwinterConfig, PoolView
from waggle.clock import Clock
from waggle.ids import CellId

# What `admit` calls before recording a Cell dormant: stop every sub-bee, remove scratch. Owned by
# whichever composition root wires the pool (the lifecycle, ultimately), since only it holds a
# session on the Cell being admitted -- the pool itself never opens one (module docstring).
Scrubber = Callable[[Cell], Awaitable[None]]

__all__ = ["DormantCell", "OverwinterPool", "PooledCandidate", "Scrubber"]


@dataclass(frozen=True, slots=True)
class DormantCell:
    """One dormant Cell as `admit`/`claim`/`claim_by_id` hand it back.

    Attributes:
        cell: The Cell itself, as it was when admitted (its own state may have moved on since;
            this is a snapshot, not a live view).
        spec: The `VirtualCellSpec` it was provisioned from, so a caller that resumes it knows the
            image and resources without a second lookup.
        dormant_since: When `admit` recorded it dormant.
        dormant_until: When `CellLifecycle.evict_expired` tears it down if nothing claims it first
            (`dormant_since + OverwinterConfig.max_dormant_s`).
    """

    cell: Cell
    spec: VirtualCellSpec
    dormant_since: datetime
    dormant_until: datetime


@dataclass(frozen=True, slots=True)
class PooledCandidate:
    """One dormant Cell, just enough for a Layer-6 caller to build a placement candidate from.

    Deliberately not `hivemind.queen.placement.inventory.DormantCandidate` (this module's own
    docstring explains why); a Layer-6 caller converts one of these into one of those, filling
    `warden_id=None` the same way that type's own docstring already documents for a Cell nobody
    has reconnected a Warden to yet.

    Attributes:
        cell_id: The dormant Cell.
        image: The image it was provisioned from.
        comb_shield: Its own security tier; never NIGHT_VEIL (`admit` refuses one outright).
    """

    cell_id: CellId
    image: str
    comb_shield: CombShieldLevel


@dataclass(slots=True)
class _Entry:
    """This pool's own bookkeeping for one dormant Cell; never exposed outside this file."""

    cell: Cell
    spec: VirtualCellSpec
    dormant_since: datetime
    dormant_until: datetime


class OverwinterPool:
    """Track which Virtual Cells are dormant, since when and until when -- bookkeeping only.

    Owns its own mutable state in place (codingrules section 8.5): `_entries`, one row per
    currently-dormant Cell, added by `admit` and removed by `claim`, `claim_by_id` or
    `evict_expired`. Never calls a `CellBackend` and never writes to the Pheromone Trail
    (`hivemind.hive.lifecycle.CellLifecycle` does both, immediately around each call into this
    pool -- module docstring).
    """

    def __init__(self, clock: Clock, config: OverwinterConfig) -> None:
        """Build an OverwinterPool with nothing dormant yet.

        Args:
            clock: Source of every timestamp this pool records or compares against.
            config: The manifest's own `[virtual_cells.overwinter]` bounds; `max_dormant_s` sets
                every admitted Cell's own `dormant_until`.
        """
        self._clock = clock
        self._config = config
        self._entries: dict[CellId, _Entry] = {}

    async def admit(self, cell: Cell, spec: VirtualCellSpec, *, scrub: Scrubber) -> DormantCell:
        """Scrub and record `cell` as dormant; the caller pauses it on the backend around this.

        The caller (`hivemind.hive.lifecycle.CellLifecycle.overwinter`) is the one that already
        ran `hivemind.hive.overwinter.policy.decide_release` and got `OVERWINTER` back; this
        method trusts that decision and does not re-check pool capacity itself (codingrules
        section 8.3: the pure decision already checked room).

        Args:
            cell: The released Cell to keep dormant; must not be NIGHT_VEIL.
            spec: The `VirtualCellSpec` it was provisioned from.
            scrub: Stops every sub-bee and removes scratch (ADR-0029: "every sub-bee is stopped,
                scratch is removed... before the backend pauses the Cell" -- the pause itself now
                happens in the caller, immediately after this returns).

        Returns:
            The DormantCell this pool now holds for `cell.id`.

        Raises:
            hivemind.hive.errors.InvalidCellTransitionError: `cell.comb_shield` is NIGHT_VEIL
                (ADR-0029: Night Veil Cells are excluded from the pool outright).
        """
        # Both checks run before the one side effect this method still performs (scrub): a
        # NIGHT_VEIL Cell or an abstractly-illegal edge must never even touch a sub-bee on its
        # way to failing.
        assert_dormant_allowed(cell.comb_shield, cell_id=cell.id)
        assert_transition(VirtualCellStatus.RELEASED, VirtualCellStatus.DORMANT, cell_id=cell.id)
        # External await: stops sub-bees and clears scratch over the Cell's own session; bounded
        # by whatever timeout the injected Scrubber's own caller (the lifecycle) already applies.
        await scrub(cell)
        now = self._clock.now()
        dormant_until = now + timedelta(seconds=self._config.max_dormant_s)
        self._entries[cell.id] = _Entry(
            cell=cell, spec=spec, dormant_since=now, dormant_until=dormant_until
        )
        return DormantCell(cell=cell, spec=spec, dormant_since=now, dormant_until=dormant_until)

    async def claim(self, image: str) -> DormantCell | None:
        """Select and remove the oldest dormant Cell provisioned from `image`, or return None.

        The caller (`hivemind.hive.lifecycle.CellLifecycle.claim_for_image`) resumes it on the
        backend and waits for its Warden's fresh Heartbeat before granting it; this method only
        performs the selection.

        Args:
            image: The image a fresh placement decision asked for.

        Returns:
            The oldest matching DormantCell, now removed from this pool; None when no dormant
            Cell was provisioned from `image`.
        """
        candidates = [entry for entry in self._entries.values() if entry.spec.image == image]
        if not candidates:
            return None  # Nothing to reuse; the caller falls through to a fresh provision.
        # Oldest first (ADR-0029 does not specify a tie-break beyond "the oldest matching Cell").
        oldest = min(candidates, key=lambda entry: entry.dormant_since)
        return await self.claim_by_id(oldest.cell.id)

    async def claim_by_id(self, cell_id: CellId) -> DormantCell | None:
        """Remove and return the dormant entry for `cell_id`, or None if it holds none.

        Used by `hivemind.hive.lifecycle.CellLifecycle.resume` when the caller already knows
        which dormant Cell to resume (placement's own `ReuseDormant` outcome names a specific
        `cell_id`), unlike `claim`, which searches by image alone. Also `claim`'s own last step,
        once it has picked the oldest matching entry.

        Args:
            cell_id: The dormant Cell to remove.

        Returns:
            The removed DormantCell, or None when `cell_id` holds no dormant entry (already
            claimed, evicted, or never admitted -- the caller treats this the same way `claim`
            treats "nothing matched").
        """
        entry = self._entries.get(cell_id)
        if entry is None:
            return None
        assert_transition(VirtualCellStatus.DORMANT, VirtualCellStatus.READY, cell_id=cell_id)
        del self._entries[cell_id]
        return DormantCell(
            cell=entry.cell,
            spec=entry.spec,
            dormant_since=entry.dormant_since,
            dormant_until=entry.dormant_until,
        )

    async def evict_expired(self, now: datetime) -> list[CellId]:
        """Remove every dormant entry whose own `dormant_until` is at or before `now`.

        The caller (`hivemind.hive.lifecycle.CellLifecycle.evict_expired`) tears each returned id
        down on the backend; this method only decides which ids are past their own deadline and
        removes their bookkeeping.

        Args:
            now: The reference time to compare every entry's `dormant_until` against (an injected
                Clock reading, so a test controls it exactly).

        Returns:
            Every expired Cell's id, in no particular order; empty when nothing has expired.
        """
        expired = [entry for entry in self._entries.values() if entry.dormant_until <= now]
        evicted: list[CellId] = []
        for entry in expired:
            assert_transition(
                VirtualCellStatus.DORMANT, VirtualCellStatus.DESTROYING, cell_id=entry.cell.id
            )
            del self._entries[entry.cell.id]
            evicted.append(entry.cell.id)
        return evicted

    def view(self) -> PoolView:
        """Return this pool's current occupancy, for `hivemind.hive.overwinter.policy.PoolView`.

        Returns:
            Total dormant count, per-image count and disk used (megabytes), summed from every
            entry's own `VirtualCellSpec`.
        """
        per_image: dict[str, int] = {}
        disk_used_mb = 0
        for entry in self._entries.values():
            per_image[entry.spec.image] = per_image.get(entry.spec.image, 0) + 1
            disk_used_mb += entry.spec.disk_bytes // (1024**2)
        return PoolView(total=len(self._entries), per_image=per_image, disk_used_mb=disk_used_mb)

    def dormant_candidates(self) -> tuple[PooledCandidate, ...]:
        """Return every dormant Cell as a PooledCandidate, for a Layer-6 placement snapshot.

        Returns:
            One PooledCandidate per currently-dormant Cell, in no particular order (module
            docstring's own "Key invariants": never `hivemind.queen.placement.inventory.
            DormantCandidate` directly -- a Layer-6 caller converts).
        """
        return tuple(
            PooledCandidate(
                cell_id=entry.cell.id, image=entry.spec.image, comb_shield=entry.cell.comb_shield
            )
            for entry in self._entries.values()
        )

    async def admit_all_idle(
        self, idle: Sequence[tuple[Cell, VirtualCellSpec]], scrub: Scrubber
    ) -> tuple[CellId, ...]:
        """Admit every (Cell, spec) pair Clustering considers idle; see `_admit_idle_batch`."""
        return await _admit_idle_batch(self, idle, scrub)


async def _admit_idle_batch(
    pool: OverwinterPool, idle: Sequence[tuple[Cell, VirtualCellSpec]], scrub: Scrubber
) -> tuple[CellId, ...]:
    """Admit every (Cell, spec) pair Clustering considers idle during a long outage.

    The hook Clustering's own long-outage pause calls instead of destroying every idle Virtual
    Cell outright (ADR-0029: "Clustering uses the pool for long outages"); wiring a trigger in
    `queen/cluster/` to call this is a separate, concurrent dispatch's job, not this one's -- this
    is only the batch entry point `OverwinterPool.admit_all_idle` delegates to (kept as a module
    function, not a method, so that method's own docstring stays short enough for codingrules
    5.1's class-length limit). A NIGHT_VEIL Cell passed in by mistake (the caller's own filtering
    should already exclude one) is skipped rather than raised through, since Clustering's own
    pause sweeps many Cells in one pass and one bad entry should not abort the rest. The caller is
    still responsible for pausing each admitted Cell on its own backend, the same as `admit`'s own
    one caller (`hivemind.hive.lifecycle.CellLifecycle.overwinter`).

    Args:
        pool: The pool `admit` is called on for each pair.
        idle: Every (Cell, VirtualCellSpec) pair to admit.
        scrub: The same Scrubber every `admit` call in this batch uses.

    Returns:
        The ids actually admitted, in `idle`'s own order; an id skipped for being NIGHT_VEIL is
        simply absent (the caller's own Undertaker call handles it as an ordinary teardown).
    """
    admitted: list[CellId] = []
    for cell, spec in idle:
        try:
            await pool.admit(cell, spec, scrub=scrub)
        except InvalidCellTransitionError:
            continue  # NIGHT_VEIL (or an already-moved Cell): leave it for teardown instead.
        admitted.append(cell.id)
    return tuple(admitted)
