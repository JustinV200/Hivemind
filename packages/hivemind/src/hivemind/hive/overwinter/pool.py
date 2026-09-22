"""Define OverwinterPool: the effectful half of Overwintering -- pause, resume, evict, list.

`hivemind.hive.overwinter.policy.decide_release` is the pure decision; this module is what acts on
it (codingrules section 8.3, "pure core, effectful edges"). `OverwinterPool` holds every dormant
Virtual Cell this Hive currently keeps paused for reuse: `admit` scrubs a released Cell (stops its
sub-bees, removes scratch -- performed by an injected `Scrubber`, since the pool never opens a
session of its own) and pauses it through the backend; `claim` picks the oldest dormant Cell for a
requested image, resumes it, and hands it back for the caller (`hivemind.hive.lifecycle`, a
concurrent dispatch) to wait on its Warden's Heartbeat and issue a fresh grant; `evict_expired`
destroys every Cell whose own `dormant_until` has passed, for the Undertaker's sweep
(`hivemind.workers.roles.undertaker.sweep.sweep_orphans`) to call; `view` reports occupancy for
`hivemind.hive.overwinter.policy.decide_release`'s own `PoolView` argument; `dormant_candidates`
reports every dormant Cell as a `PooledCandidate` for whichever Layer-6 caller builds a placement
snapshot (module docstring's own "Key invariants" explains why this is not literally
`hivemind.queen.placement.inventory.DormantCandidate`).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.hive.overwinter`.
    Called by `hivemind.hive.lifecycle` (roadmap step 5.6, a concurrent dispatch) on release
    (`admit`) and on placement's own `ReuseDormant` outcome (`claim`), and by
    `hivemind.workers.roles.undertaker.sweep.sweep_orphans` (`evict_expired`) and whichever Layer-6
    caller builds `hivemind.queen.placement.inventory.Inventory.dormant` (`dormant_candidates`,
    `view`). Calls into `hivemind.cell` (Cell, CellIdentity), `hivemind.hive.backends.base`
    (CellBackend), `hivemind.hive.cell_state` (VirtualCellStatus, assert_dormant_allowed,
    assert_transition), `hivemind.hive.errors` (InvalidCellTransitionError),
    `hivemind.hive.models` (VirtualCellSpec), `hivemind.hive.overwinter.policy` (OverwinterConfig,
    PoolView), `hivemind.pheromone` (CellEvent, PheromoneTrail) and waggle only.

Key invariants:
    - `admit` never admits a `CombShieldLevel.NIGHT_VEIL` Cell: `cell_state.assert_dormant_allowed`
      raises `InvalidCellTransitionError` before the backend is ever paused (ADR-0029, codingrules
      section 8.7).
    - `admit`/`claim`/`evict_expired` each call `hivemind.hive.cell_state.assert_transition` for
      the abstract edge they perform (RELEASED->DORMANT, DORMANT->READY, DORMANT->DESTROYING)
      before the backend call, the same "validate the edge, then act" order every state machine
      in this repository follows -- even though the table always allows these three edges, so the
      call is a consistency guard against a future table change, not a live check today.
    - `dormant_candidates` returns this module's own `PooledCandidate`, never `hivemind.queen.
      placement.inventory.DormantCandidate` directly: `hive` is Layer 3 and `queen` is Layer 6
      (codingrules section 4's layer table, enforced by `lint-imports`'s "layers" contract), so a
      Layer-3 module may never import a Layer-6 one. The Layer-6 caller that builds an `Inventory`
      already imports both packages and converts one `PooledCandidate` into one `DormantCandidate`
      per row (`warden_id=None`, matching that type's own "commonly None until then" docstring).
    - Every admitted Cell's disk (`VirtualCellSpec.disk_bytes`) and image are what `view()` sums
      for `hivemind.hive.overwinter.policy.PoolView`; `evict_expired` and `claim` both remove the
      entry before returning, so occupancy is never double-counted mid-call.

See Also:
    - docs/adr/0029-overwintering-policy.md for the pool this module implements.
    - hivemind.hive.overwinter.policy for decide_release, OverwinterConfig and PoolView, this
      module's pure counterpart.
    - hivemind.hive.backends.base for CellBackend, whose pause/resume/destroy this module calls.
    - hivemind.hive.cell_state for the Virtual Cell state machine this module's three moves follow.
    - hivemind.workers.roles.undertaker.sweep for sweep_orphans, evict_expired's one caller.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import JsonValue

from hivemind.cell import Cell, CellIdentity, CombShieldLevel
from hivemind.hive.backends.base import CellBackend
from hivemind.hive.cell_state import VirtualCellStatus, assert_dormant_allowed, assert_transition
from hivemind.hive.errors import InvalidCellTransitionError
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import OverwinterConfig, PoolView
from hivemind.pheromone import CellEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, new_event_id

# What `admit` calls before pausing a Cell: stop every sub-bee, remove scratch. Owned by whichever
# composition root wires the pool (the lifecycle or a Warden), since only they hold a session on
# the Cell being admitted -- the pool itself never opens one (module docstring).
Scrubber = Callable[[Cell], Awaitable[None]]

__all__ = ["DormantCell", "OverwinterPool", "PooledCandidate", "Scrubber"]


@dataclass(frozen=True, slots=True)
class DormantCell:
    """One dormant Cell as `admit`/`claim` hand it back: the Cell, its spec, and its own deadline.

    Attributes:
        cell: The Cell itself, as it was when admitted (its own state may have moved on since;
            this is a snapshot, not a live view).
        spec: The `VirtualCellSpec` it was provisioned from, so a caller that resumes it knows the
            image and resources without a second lookup.
        dormant_since: When `admit` paused it.
        dormant_until: When the Undertaker's `evict_expired` sweep destroys it if nothing claims
            it first (`admit_since + OverwinterConfig.max_dormant_s`).
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
    """Hold every dormant Virtual Cell this Hive keeps paused for reuse, and act on it.

    Owns its own mutable state in place (codingrules section 8.5): `_entries`, one row per
    currently-dormant Cell, added by `admit` and removed by `claim` or `evict_expired`.
    """

    def __init__(
        self,
        backend: CellBackend,
        clock: Clock,
        config: OverwinterConfig,
        trail: PheromoneTrail,
        identity: CellIdentity,
    ) -> None:
        """Build an OverwinterPool with nothing dormant yet.

        Args:
            backend: Where `pause`/`resume`/`destroy` are actually performed.
            clock: Source of every timestamp this pool records or compares against.
            config: The manifest's own `[virtual_cells.overwinter]` bounds; `max_dormant_s` sets
                every admitted Cell's own `dormant_until`.
            trail: Where `cell.overwintered`/`cell.ready`/`cell.destroyed` events land.
            identity: The Hive, node and actor this pool stamps on every trail event it writes.
        """
        self._backend = backend
        self._clock = clock
        self._config = config
        self._trail = trail
        self._identity = identity
        self._entries: dict[CellId, _Entry] = {}

    async def admit(self, cell: Cell, spec: VirtualCellSpec, *, scrub: Scrubber) -> DormantCell:
        """Scrub, pause and record `cell` as dormant, for fast reuse later.

        The caller (`hivemind.hive.lifecycle`) is the one that already ran `hivemind.hive.
        overwinter.policy.decide_release` and got `OVERWINTER` back; this method trusts that
        decision and does not re-check pool capacity itself (codingrules section 8.3: the pure
        decision already checked room, so re-checking here would just be the same rule twice).

        Args:
            cell: The released Cell to keep dormant; must not be NIGHT_VEIL.
            spec: The `VirtualCellSpec` it was provisioned from.
            scrub: Stops every sub-bee and removes scratch before the backend pauses the Cell
                (ADR-0029: "every sub-bee is stopped, scratch is removed... before the backend
                pauses the Cell").

        Returns:
            The DormantCell this pool now holds for `cell.id`.

        Raises:
            hivemind.hive.errors.InvalidCellTransitionError: `cell.comb_shield` is NIGHT_VEIL
                (ADR-0029: Night Veil Cells are excluded from the pool outright).
            hivemind.hive.errors.BackendCapabilityError: The backend cannot pause a Cell at all.
        """
        # Both checks run before any side effect (scrub, pause): a NIGHT_VEIL Cell or an
        # abstractly-illegal edge must never touch a sub-bee or the backend on its way to failing.
        assert_dormant_allowed(cell.comb_shield, cell_id=cell.id)
        assert_transition(VirtualCellStatus.RELEASED, VirtualCellStatus.DORMANT, cell_id=cell.id)
        # External await: stops sub-bees and clears scratch over the Cell's own session; bounded
        # by whatever timeout the injected Scrubber's own caller (the lifecycle) already applies.
        await scrub(cell)
        # External await: the backend's own pause call; raises BackendCapabilityError if the
        # backend declared can_pause=False, which the caller's own decide_release should already
        # have ruled out via ReleaseOutcome.backend_can_pause.
        await self._backend.pause(cell.id)
        now = self._clock.now()
        dormant_until = now + timedelta(seconds=self._config.max_dormant_s)
        self._entries[cell.id] = _Entry(
            cell=cell, spec=spec, dormant_since=now, dormant_until=dormant_until
        )
        await self._record(
            "cell.overwintered",
            cell.id,
            {"image": spec.image, "dormant_until": dormant_until.isoformat()},
        )
        return DormantCell(cell=cell, spec=spec, dormant_since=now, dormant_until=dormant_until)

    async def claim(self, image: str) -> DormantCell | None:
        """Resume the oldest dormant Cell provisioned from `image`, or return None.

        The caller (placement's own `ReuseDormant` outcome, via the lifecycle) waits for the
        resumed Cell's Warden to reconnect and send a fresh Heartbeat before granting it (ADR-0029:
        "the lifecycle resumes it, waits for the Warden's heartbeat, and issues a fresh grant");
        this method only performs the backend-side resume.

        Args:
            image: The image a fresh placement decision asked for.

        Returns:
            The oldest matching DormantCell, now resumed and removed from this pool; None when no
            dormant Cell was provisioned from `image`.
        """
        candidates = [entry for entry in self._entries.values() if entry.spec.image == image]
        if not candidates:
            return None  # Nothing to reuse; the caller falls through to a fresh provision.
        # Oldest first (ADR-0029 does not specify a tie-break beyond "the oldest matching Cell"):
        # a Cell that has sat dormant longest is the one whose own dormant_until is soonest, so
        # claiming it first also frees the eviction sweep from having to destroy it later.
        oldest = min(candidates, key=lambda entry: entry.dormant_since)
        assert_transition(
            VirtualCellStatus.DORMANT, VirtualCellStatus.READY, cell_id=oldest.cell.id
        )
        # External await: the backend's own resume call; the caller still waits for a Heartbeat
        # before trusting the Cell is actually reachable again (module docstring).
        await self._backend.resume(oldest.cell.id)
        del self._entries[oldest.cell.id]
        # cell.ready is the nearest existing kind: cell_state.TRANSITIONS' own DORMANT->READY edge
        # is commented "resumed from the pool: the backend woke it and reconnected", the same
        # meaning cell.ready already carries for a freshly-provisioned Cell's first Heartbeat. A
        # dedicated cell.resumed kind would say this more precisely (reported to the orchestrator).
        await self._record("cell.ready", oldest.cell.id, {"image": image, "from_pool": True})
        return DormantCell(
            cell=oldest.cell,
            spec=oldest.spec,
            dormant_since=oldest.dormant_since,
            dormant_until=oldest.dormant_until,
        )

    async def evict_expired(self, now: datetime) -> list[CellId]:
        """Destroy every dormant Cell whose own `dormant_until` is at or before `now`.

        Called by `hivemind.workers.roles.undertaker.sweep.sweep_orphans`'s own dormant-eviction
        phase; the effects happen here, so that caller only has to report the ids this returns.

        Args:
            now: The reference time to compare every entry's `dormant_until` against (an injected
                Clock reading, so a test controls it exactly).

        Returns:
            Every evicted Cell's id, in no particular order; empty when nothing has expired.
        """
        expired = [entry for entry in self._entries.values() if entry.dormant_until <= now]
        evicted: list[CellId] = []
        for entry in expired:
            assert_transition(
                VirtualCellStatus.DORMANT, VirtualCellStatus.DESTROYING, cell_id=entry.cell.id
            )
            # External await: the backend's own destroy call; idempotent (CellBackend's own
            # contract), so a Cell this pool no longer tracks by the time this runs is still safe.
            await self._backend.destroy(entry.cell.id)
            del self._entries[entry.cell.id]
            await self._record("cell.destroyed", entry.cell.id, {"reason": "dormant_expired"})
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

    async def _record(self, kind: str, cell_id: CellId, payload: Mapping[str, JsonValue]) -> None:
        """Build and record a CellEvent for `cell_id`, stamped with this pool's own identity."""
        event = CellEvent(
            id=new_event_id(self._clock),
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            at=self._clock.now(),
            actor=self._identity.actor,
            kind=kind,
            subject_id=cell_id,
            payload=dict(payload),
        )
        await self._trail.record(event)


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
    pause sweeps many Cells in one pass and one bad entry should not abort the rest.

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
