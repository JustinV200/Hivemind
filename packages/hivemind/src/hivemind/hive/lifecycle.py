"""Define CellLifecycle: drive a Virtual Cell from provision through grant to overwinter/teardown.

Roadmap step 5.6: "provision -> Warden ready -> grant -> release -> (overwinter | teardown), each
transition a cell.* event. Night Veil path is always provision -> Warden ready -> grant -> teardown
(no Overwinter branch)." `hivemind.hive.cell_state` already defines the one state machine
(`VirtualCellStatus`/`TRANSITIONS`) this module drives; `CellLifecycle` is its only intended caller
(that module's own docstring). It owns the table of live Virtual Cells in memory (`LiveVirtualCell`
per `waggle.ids.CellId`), reconciled from every registered backend's own `list_cells` on start
(codingrules Appendix C: "Backend labels are the source of truth; the table is reconciled against
them"), and exposes one method per edge, each calling `hivemind.hive.cell_state.assert_transition`
(or `assert_dormant_allowed` for the Night Veil guard) and recording exactly one `cell.*`
`hivemind.pheromone.CellEvent` before returning. The class itself is a thin shell: every edge's
real logic and its own heavily-commented docstring live in a module-level function below it
(`_provision`, `_mark_ready`, ...), the same "class reads and writes its own state, plain functions
do the work" split `hivemind.wardens.warden.Warden` uses for its own tick dispatch, needed here to
keep `CellLifecycle` itself under codingrules 5.1's 200-line class limit.

**Reconciled with `hivemind.hive.overwinter.pool.OverwinterPool` (this same dispatch):**
`CellLifecycle` now owns every state edge *and* every backend call, including the ones that used to
live on `OverwinterPool` (`pause`/`resume`/`destroy` for an Overwintered Cell). `OverwinterPool`
itself is left with bookkeeping and selection only -- see its own module docstring. Concretely:
`overwinter(cell_id, *, scrub)` calls `pool.admit` (bookkeeping) then `backend.pause` (the effect)
then moves RELEASED -> DORMANT; `resume(cell_id, ...)` calls `pool.claim_by_id` (bookkeeping,
best-effort: a Cell resumed without ever having been admitted, e.g. a direct call in a test, simply
finds nothing there) then `backend.resume` then moves DORMANT -> READY; `claim_for_image(image)` is
the convenience path for a caller that only has an image, not a specific `cell_id` (asks the pool
which Cell is oldest, then calls `resume` on it); `evict_expired(now)` calls `pool.evict_expired`
(bookkeeping: which ids are past their own deadline) then `teardown`s each one, which is what
actually calls `backend.destroy`. `release(cell_id, outcome)` replaces the old injected
`DecideRelease` hook with a direct call to `hivemind.hive.overwinter.policy.decide_release`, built
from the caller's own `ReleaseOutcome`, the injected `OverwinterSettings.pool.view()` and its
`config` -- both modules are Layer 3, so this import is legal (codingrules section 4). A
`CellLifecycle` built with `overwinter=None` always tears down on release, the same behaviour the
old `always_teardown` default hook gave; this is the only place that default still exists.

`provision()` and `mark_ready()` are deliberately two calls, not one, even though a `CellBackend.
provision()` call already blocks until its own `ReadinessGate` sees a signed `CellReady` and the
first `CellHeartbeat` (`hivemind.hive.backends.base.CellBackend.provision`'s own contract,
ADR-0027): the roadmap's own wording names "Warden ready" as its own milestone, and `hivemind.
queen.cell_gate.provider.LifecycleVirtualCellProvider` needs a point after `provision()` where it
has also confirmed the Queen-side listener produced a live `WardenLink` for this Cell before the
Cell counts as truly READY for placement. `mark_ready()` is that second, separate edge.

The Night Veil rule is enforced independently at two points regardless of what the pool or the
policy decide (`hivemind.hive.cell_state.can_enter_dormant`/`assert_dormant_allowed`): `release()`
short-circuits to teardown before ever calling `decide_release` for a NIGHT_VEIL Cell, and
`overwinter()` refuses one directly too, so neither path depends on the other to keep the rule
(codingrules section 8.7, "Night Veil lifecycle is teardown-only").

Fits into the Hive:
    Layer 3 (sources of Cells). Called by `hivemind.queen.cell_gate.provider.
    LifecycleVirtualCellProvider`, `hivemind.workers.roles.undertaker` (the Queen-startup sweep's
    dormant eviction, via `evict_expired`) and `hive cells` commands (5.13). Calls into
    `hivemind.cell` (Cell, CellIdentity, CombShieldLevel), `hivemind.hive.backends` (
    BackendCapabilities, VirtualCellRecord), `hivemind.hive.cell_state`, `hivemind.hive.errors`,
    `hivemind.hive.models`, `hivemind.hive.overwinter` (OverwinterConfig, OverwinterDecision,
    OverwinterPool, ReleaseOutcome, Scrubber, decide_release), `hivemind.hive.registry`,
    `hivemind.hive.snapshot.ledger` (SnapshotLedgerPort, roadmap step 5.10: `teardown()`'s own
    snapshot cleanup), `hivemind.hive.backends.base` (CellBackend, `snapshot_target`'s own return
    type), `hivemind.pheromone` (CellEvent, PheromoneTrail) and waggle only.

Key invariants:
    - Every state change goes through `hivemind.hive.cell_state.assert_transition` (or
      `assert_dormant_allowed` for DORMANT) before this module's own in-memory table is updated,
      and the matching `cell.*` event is recorded in the same call, before the method returns.
    - `self._cells` is keyed by the backend's own minted `CellId`; a failed `provision()` never
      adds an entry (the backend itself already cleaned up any partial resource, per `CellBackend.
      provision`'s own contract), so there is never a lingering FAILED row to sweep.
    - A record whose `hivemind.cell.CombShieldLevel` is NIGHT_VEIL never reaches DORMANT: `release`
      never returns `OverwinterDecision.OVERWINTER` for one, and `overwinter()` itself refuses one
      directly too, so neither path depends on the other to keep the rule.
    - Every `CellBackend` call this class used to delegate to `OverwinterPool` now happens here,
      immediately around the matching pool bookkeeping call, so the abstract state
      (`VirtualCellStatus`) and the backend's own real state never drift mid-call.

See Also:
    - .claude/roadmap.md step 5.6 for the edge sequence this module implements almost verbatim.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for why `provision()`
      already implies "Warden ready" at the backend level, and why `mark_ready()` still exists.
    - docs/adr/0029-overwintering-policy.md for decide_release, the release-decision this module
      now calls directly.
    - hivemind.hive.cell_state for VirtualCellStatus, TRANSITIONS and the Night Veil guard.
    - hivemind.hive.overwinter for OverwinterPool (bookkeeping/selection) and decide_release (the
      pure policy), this module's two Layer-3 collaborators.
    - hivemind.queen.cell_gate.provider for LifecycleVirtualCellProvider, this module's Queen-side
      caller.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from hivemind.cell import Cell, CellIdentity, CombShieldLevel
from hivemind.hive.backends.base import BackendCapabilities, CellBackend, VirtualCellRecord
from hivemind.hive.cell_state import (
    VirtualCellStatus,
    assert_dormant_allowed,
    assert_transition,
    can_enter_dormant,
)
from hivemind.hive.errors import CellProvisionError, InvalidCellTransitionError, UnknownCellError
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import (
    OverwinterConfig,
    OverwinterDecision,
    ReleaseOutcome,
    decide_release,
)
from hivemind.hive.overwinter.pool import OverwinterPool, Scrubber
from hivemind.hive.registry import BackendRegistry
from hivemind.hive.snapshot.ledger import SnapshotLedgerPort
from hivemind.pheromone import CellEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, GrantId, HiveId, WardenId, new_event_id

__all__ = [
    "CellLifecycle",
    "LifecycleDormantCell",
    "LifecycleVirtualBackend",
    "LiveVirtualCell",
    "OverwinterSettings",
]


@dataclass(frozen=True, slots=True)
class OverwinterSettings:
    """The pool and config `CellLifecycle` needs to ever decide OVERWINTER; both or neither.

    Bundled into one constructor parameter (codingrules 5.1's five-parameter limit) and to make
    "both or neither" structural rather than a runtime check: a `CellLifecycle` built with no
    `OverwinterSettings` at all always tears down on release (the old `always_teardown` default's
    behaviour), and one built with one always has both a pool to admit into and a config to decide
    against.

    Attributes:
        pool: Bookkeeping and selection for dormant Cells (`hivemind.hive.overwinter.pool.
            OverwinterPool`).
        config: The manifest's own `[virtual_cells.overwinter]` bounds, passed to
            `hivemind.hive.overwinter.policy.decide_release` on every `release()` call.
    """

    pool: OverwinterPool
    config: OverwinterConfig


@dataclass(slots=True)
class LiveVirtualCell:
    """One Virtual Cell as `CellLifecycle` currently tracks it -- mutable, owned by it alone.

    Codingrules section 8.5 allows this: a class that owns its own mutable state in place,
    documented, the same shape `hivemind.cell.lease.RealCellLease` uses for a lease's own state.

    Attributes:
        cell_id: This record's own key into `CellLifecycle`'s table.
        status: This Cell's current `VirtualCellStatus`; every change goes through
            `hivemind.hive.cell_state.assert_transition` first.
        backend: The `hivemind.hive.backends.base.CellBackend.name` this Cell was provisioned
            from (or last seen on, for a reconciled record).
        image: The `VirtualCellSpec.image` this Cell was provisioned from.
        comb_shield: This Cell's own security tier; checked by `overwinter()`'s own Night Veil
            guard without needing `spec` or `cell` to be populated.
        cell: The full `hivemind.cell.Cell` this process provisioned, once known. None for a
            record `reconcile()` only learned about from backend labels: `CellBackend.list_cells`
            returns a `VirtualCellRecord`, not a full Cell (module docstring).
        spec: The `VirtualCellSpec` this Cell was provisioned from, when this process is the one
            that provisioned it. None for a reconciled record, since a `VirtualCellSpec` is never
            persisted anywhere a restarted Queen could read it back from (codingrules Appendix C
            lists no store for it); a record with no `spec` can never be Overwintered (`release`
            falls back to teardown for one -- decide_release has nothing to check against).
        warden_id: The attached Warden's own id, once a `WardenLink` exists for this Cell.
        grant_id: The live `ForageGrant` id, once `grant()` has been called.
    """

    cell_id: CellId
    status: VirtualCellStatus
    backend: str
    image: str
    comb_shield: CombShieldLevel
    cell: Cell | None = None
    spec: VirtualCellSpec | None = None
    warden_id: WardenId | None = None
    grant_id: GrantId | None = None


@dataclass(frozen=True, slots=True)
class LifecycleDormantCell:
    """One dormant Virtual Cell, as `dormant_candidates()` reports it.

    A hive-layer value, deliberately not `hivemind.queen.placement.inventory.DormantCandidate`
    itself: `hive` is Layer 3 and `queen.placement` is Layer 6, so this module may never import
    it (codingrules section 4). The composition root converts one of these into the queen-layer
    type once it already holds both.

    Attributes:
        cell_id: The dormant Cell itself.
        warden_id: Its own Warden, if still known (see `LiveVirtualCell.warden_id`).
        image: The image it was provisioned from.
        comb_shield: Its own security tier; never NIGHT_VEIL (the module invariant).
    """

    cell_id: CellId
    warden_id: WardenId | None
    image: str
    comb_shield: CombShieldLevel


@dataclass(frozen=True, slots=True)
class LifecycleVirtualBackend:
    """One registered backend, as `virtual_backend_candidates()` currently sees it.

    Also deliberately hive-layer, not `hivemind.queen.placement.inventory.VirtualBackendCandidate`
    (see `LifecycleDormantCell`'s own docstring for why); `capabilities.headroom` here is already
    narrowed to this backend's *remaining* room (its own declared cap minus how many Cells this
    lifecycle currently tracks on it), matching what the queen-layer type's own docstring requires
    of its caller.

    Attributes:
        name: The backend's own registry name.
        capabilities: What this backend can do, with `headroom` narrowed to remaining room.
    """

    name: str
    capabilities: BackendCapabilities


class CellLifecycle:
    """Own the live table of Virtual Cells; every edge's own logic lives in a module function.

    Not safe to share a single `LiveVirtualCell` record across concurrent edge calls for the same
    `cell_id` (the same caveat `hivemind.cell.lease.RealCellLease` carries): a Virtual Cell has
    exactly one Warden and one placement decision touching it at a time in practice, so no lock is
    taken here, mirroring every other lifecycle class in this codebase.
    """

    def __init__(
        self,
        registry: BackendRegistry,
        trail: PheromoneTrail,
        clock: Clock,
        identity: CellIdentity,
        *,
        overwinter: OverwinterSettings | None = None,
    ) -> None:
        """Build a CellLifecycle with an empty table; call `reconcile()` before relying on it.

        Args:
            registry: Every registered `CellBackend`, looked up by name on every edge.
            trail: Where every `cell.*` event this lifecycle drives lands.
            clock: Source of every minted event id and timestamp.
            identity: The Hive, node and actor this lifecycle stamps on every trail event.
            overwinter: The pool and config `release()` needs to ever decide OVERWINTER; None
                means every released Cell is torn down (the old `always_teardown` default's
                behaviour).
        """
        self._registry = registry
        self._trail = trail
        self._clock = clock
        self._identity = identity
        self._pool = overwinter.pool if overwinter is not None else None
        self._overwinter_config = overwinter.config if overwinter is not None else None
        # Roadmap step 5.10: set post-construction via attach_snapshot_ledger, not a constructor
        # parameter -- __init__ is already at codingrules 5.1's five-parameter limit with
        # registry/trail/clock/identity/overwinter, and this field is optional for every Hive
        # that has no SnapshotLedger yet (every pre-5.10 caller keeps building unchanged).
        self._snapshot_ledger: SnapshotLedgerPort | None = None
        self._cells: dict[CellId, LiveVirtualCell] = {}

    def attach_snapshot_ledger(self, ledger: SnapshotLedgerPort) -> None:
        """Attach the Hive's shared SnapshotLedger, so `teardown()` deletes a Cell's own snapshots.

        Args:
            ledger: The `hivemind.hive.snapshot.SnapshotLedgerPort` (in-memory or durable) every
                Snapshotter this Hive builds
                shares (roadmap step 5.10). Optional: a `CellLifecycle` no one ever calls this on
                simply skips the cleanup step in `teardown()`, matching pre-5.10 behaviour.
        """
        self._snapshot_ledger = ledger

    def status_of(self, cell_id: CellId) -> VirtualCellStatus | None:
        """Return `cell_id`'s current status, or None if this lifecycle has no record of it."""
        record = self._cells.get(cell_id)
        return record.status if record is not None else None

    def snapshot_target(self, cell_id: CellId) -> tuple[CellBackend, Cell] | None:
        """Return `cell_id`'s own backend and Cell, for a snapshot or rollback request.

        Roadmap step 5.10's own follow-up gap: `hivemind.queen.cell_gate.snapshot.
        CellSnapshotHandler` calls this to answer a Warden's `CellSnapshotRequest`/
        `CellRollbackRequest` (the snapshot relay, since a Virtual Cell's own Warden cannot reach
        the host backend itself, ADR-0027) without needing a `CellLifecycle` accessor of its own
        for every field it happens to want.

        Args:
            cell_id: The Cell a snapshot or rollback was asked for.

        Returns:
            `(backend, cell)` when this lifecycle tracks `cell_id` and holds a full `Cell` for it
            (module docstring's own `LiveVirtualCell.cell`: None for a record `reconcile()` only
            learned about from backend labels); `None` otherwise -- both cases the caller answers
            with an error reply, never a crash.
        """
        record = self._cells.get(cell_id)
        if record is None or record.cell is None:
            return None
        return self._registry.get(record.backend), record.cell

    def live_cells(self) -> tuple[LiveVirtualCell, ...]:
        """Return every Cell this lifecycle currently tracks, in no particular order."""
        return tuple(self._cells.values())

    async def reconcile(self, hive_id: HiveId) -> None:
        """Rebuild the live table from every backend's own `list_cells`. See `_reconcile`."""
        await _reconcile(self, hive_id)

    async def provision(self, spec: VirtualCellSpec, backend_name: str) -> Cell:
        """Provision a fresh Virtual Cell from `spec` on `backend_name`. See `_provision`."""
        return await _provision(self, spec, backend_name)

    async def mark_ready(self, cell_id: CellId, warden_id: WardenId | None = None) -> None:
        """Move `cell_id` PROVISIONING -> READY. See `_mark_ready`."""
        await _mark_ready(self, cell_id, warden_id)

    async def resume(self, cell_id: CellId, warden_id: WardenId | None = None) -> None:
        """Move `cell_id` DORMANT -> READY. See `_resume`."""
        await _resume(self, cell_id, warden_id)

    async def claim_for_image(self, image: str) -> CellId | None:
        """Resume the oldest dormant Cell for `image` via the pool. See `_claim_for_image`."""
        return await _claim_for_image(self, image)

    async def grant(self, cell_id: CellId, grant_id: GrantId) -> None:
        """Move `cell_id` READY -> GRANTED. See `_grant`."""
        await _grant(self, cell_id, grant_id)

    async def release(self, cell_id: CellId, outcome: ReleaseOutcome) -> OverwinterDecision:
        """Move `cell_id` GRANTED -> RELEASED, then decide overwinter/teardown. See `_release`."""
        return await _release(self, cell_id, outcome)

    async def overwinter(self, cell_id: CellId, *, scrub: Scrubber) -> None:
        """Move `cell_id` RELEASED -> DORMANT via the pool and the backend. See `_overwinter`."""
        await _overwinter(self, cell_id, scrub)

    async def teardown(self, cell_id: CellId) -> None:
        """Destroy `cell_id`. See `_teardown`."""
        await _teardown(self, cell_id)

    async def evict_expired(self, now: datetime) -> tuple[CellId, ...]:
        """Tear down every dormant Cell past its own deadline. See `_evict_expired`."""
        return await _evict_expired(self, now)

    def dormant_candidates(self) -> tuple[LifecycleDormantCell, ...]:
        """Return every DORMANT Cell this lifecycle tracks. See `_dormant_candidates`."""
        return _dormant_candidates(self)

    def virtual_backend_candidates(self) -> tuple[LifecycleVirtualBackend, ...]:
        """Return every backend with its remaining headroom. See `_virtual_backend_candidates`."""
        return _virtual_backend_candidates(self)

    def _require(self, cell_id: CellId) -> LiveVirtualCell:
        """Return `cell_id`'s tracked record, or raise UnknownCellError."""
        record = self._cells.get(cell_id)
        if record is None:
            raise UnknownCellError("lifecycle", cell_id)
        return record

    async def _record(self, subject_id: str, kind: str, **payload: JsonValue) -> None:
        """Build and record one cell.* CellEvent, stamped with this lifecycle's own identity."""
        event = CellEvent(
            id=new_event_id(self._clock),
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            at=self._clock.now(),
            actor=self._identity.actor,
            kind=kind,
            subject_id=subject_id,
            payload={key: value for key, value in payload.items() if value is not None},
        )
        await self._trail.record(event)


# ──────────────────────────────────────────────────────────────────────────────
# Edge implementations: module-level so CellLifecycle's own class body stays within
# codingrules 5.1's 200-line class limit (module docstring's own note on the split).
# ──────────────────────────────────────────────────────────────────────────────


async def _reconcile(lifecycle: CellLifecycle, hive_id: HiveId) -> None:
    """Rebuild `lifecycle`'s table from every registered backend's own `list_cells(hive_id)`.

    Codingrules Appendix C: "Backend labels are the source of truth; the table is reconciled
    against them." Called once, at Queen startup, before any other method on this class.
    """
    for name in lifecycle._registry.names():
        backend = lifecycle._registry.get(name)
        for record in await backend.list_cells(hive_id):
            if record.cell_id in lifecycle._cells:
                continue  # Already known: this process provisioned it before reconciling.
            lifecycle._cells[record.cell_id] = _from_backend_record(record, name)


async def _provision(lifecycle: CellLifecycle, spec: VirtualCellSpec, backend_name: str) -> Cell:
    """Provision a fresh Virtual Cell from `spec` on `backend_name`; the first lifecycle edge.

    `backend.provision(spec)` itself already blocks until its own `ReadinessGate` has seen a
    signed `CellReady` and the first `CellHeartbeat` (`CellBackend.provision`'s own contract,
    ADR-0027), so by the time this returns the Cell is already reachable; `mark_ready()` is still
    a separate call (module docstring) for the Queen-side confirmation a `WardenLink` exists.

    Raises:
        hivemind.hive.UnknownBackendError: `backend_name` names no registered backend.
        CellProvisionError: The backend could not create the Cell, or it never became reachable;
            nothing is added to `lifecycle`'s table, and `cell.provision_failed` is recorded
            before this re-raises.
    """
    backend = lifecycle._registry.get(backend_name)
    try:
        cell = await backend.provision(spec)
    except CellProvisionError:
        # No CellId exists yet on failure (the backend mints its own, internally, and never
        # hands one back on this path): the edge is still validated in its pure form, and the
        # failure event is scoped to the Hive rather than a Cell that was never created.
        assert_transition(VirtualCellStatus.PROVISIONING, VirtualCellStatus.FAILED)
        await lifecycle._record(
            lifecycle._identity.hive_id,
            "cell.provision_failed",
            backend=backend_name,
            image=spec.image,
        )
        raise
    lifecycle._cells[cell.id] = LiveVirtualCell(
        cell_id=cell.id,
        status=VirtualCellStatus.PROVISIONING,
        backend=backend_name,
        image=spec.image,
        comb_shield=spec.comb_shield,
        cell=cell,
        spec=spec,
    )
    # Two events for one arrival (module docstring): "provisioning began" and "the backend
    # created it" are both true the instant this Cell's id is first known to this lifecycle.
    await lifecycle._record(cell.id, "cell.provisioning", backend=backend_name, image=spec.image)
    await lifecycle._record(cell.id, "cell.provisioned", backend=backend_name, image=spec.image)
    return cell


async def _mark_ready(
    lifecycle: CellLifecycle, cell_id: CellId, warden_id: WardenId | None
) -> None:
    """Move `cell_id` PROVISIONING -> READY: the Queen-side "Warden ready" edge."""
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.READY, cell_id=cell_id)
    record.status = VirtualCellStatus.READY
    if warden_id is not None:
        record.warden_id = warden_id
    await lifecycle._record(cell_id, "cell.ready", warden_id=warden_id)


async def _resume(lifecycle: CellLifecycle, cell_id: CellId, warden_id: WardenId | None) -> None:
    """Move `cell_id` DORMANT -> READY: resume an Overwintered Cell instead of provisioning.

    Removes the pool's own bookkeeping entry for `cell_id` (best-effort: a Cell resumed without
    ever going through `overwinter()` first -- a direct call in a test, say -- simply finds
    nothing there), then resumes it on the backend, then moves the abstract state.
    """
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.READY, cell_id=cell_id)
    if lifecycle._pool is not None:
        await lifecycle._pool.claim_by_id(cell_id)
    backend = lifecycle._registry.get(record.backend)
    await backend.resume(cell_id)
    record.status = VirtualCellStatus.READY
    if warden_id is not None:
        record.warden_id = warden_id
    await lifecycle._record(cell_id, "cell.resumed", warden_id=warden_id)


async def _claim_for_image(lifecycle: CellLifecycle, image: str) -> CellId | None:
    """Resume the oldest dormant Cell for `image`, via the pool, or return None.

    For a caller that only has an image, not a specific `cell_id` (unlike `resume`, which the
    caller uses when placement already named one). Asks the pool which Cell is oldest, then
    delegates the actual resume (backend call, state move, event) to `resume` itself.
    """
    if lifecycle._pool is None:
        return None  # Nothing was ever admitted without a pool; nothing to claim.
    dormant = await lifecycle._pool.claim(image)
    if dormant is None:
        return None
    await lifecycle.resume(dormant.cell.id)
    return dormant.cell.id


async def _grant(lifecycle: CellLifecycle, cell_id: CellId, grant_id: GrantId) -> None:
    """Move `cell_id` READY -> GRANTED: placement handed it to a task."""
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.GRANTED, cell_id=cell_id)
    record.status = VirtualCellStatus.GRANTED
    record.grant_id = grant_id
    await lifecycle._record(cell_id, "cell.granted", grant_id=grant_id)


async def _release(
    lifecycle: CellLifecycle, cell_id: CellId, outcome: ReleaseOutcome
) -> OverwinterDecision:
    """Move `cell_id` GRANTED -> RELEASED, then decide overwinter or teardown.

    Records `cell.virtual_released` (a distinct kind from `cell.released`, which already means "a
    Real Cell lease closed and the device restored" -- a different fact about a different kind of
    Cell). Does not itself perform the decided edge: the caller calls `overwinter()` or
    `teardown()` next, based on the returned decision.
    """
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.RELEASED, cell_id=cell_id)
    record.status = VirtualCellStatus.RELEASED
    record.grant_id = None
    await lifecycle._record(cell_id, "cell.virtual_released")
    # The Night Veil rule is enforced here, ahead of the policy, so a released Night Veil Cell is
    # never even offered to decide_release ("regardless of what the policy decides").
    if not can_enter_dormant(record.comb_shield):
        return OverwinterDecision.TEARDOWN
    if lifecycle._pool is None or lifecycle._overwinter_config is None:
        return OverwinterDecision.TEARDOWN  # No pool configured: always_teardown's old behaviour.
    if record.cell is None or record.spec is None:
        # A reconciled record this process never provisioned: decide_release has no VirtualCellSpec
        # to check against (module docstring's own "Key invariants"), so it is never Overwintered.
        return OverwinterDecision.TEARDOWN
    decision = decide_release(
        record.cell, record.spec, outcome, lifecycle._pool.view(), lifecycle._overwinter_config
    )
    return decision.decision


async def _overwinter(lifecycle: CellLifecycle, cell_id: CellId, scrub: Scrubber) -> None:
    """Move `cell_id` RELEASED -> DORMANT: admit it into the pool, then pause it on the backend."""
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.DORMANT, cell_id=cell_id)
    # A second guard, independent of release()'s own decision (module docstring): even a direct
    # call to overwinter() can never move a NIGHT_VEIL Cell to DORMANT.
    assert_dormant_allowed(record.comb_shield, cell_id=cell_id)
    if lifecycle._pool is None or record.cell is None or record.spec is None:
        raise InvalidCellTransitionError(
            record.status,
            VirtualCellStatus.DORMANT,
            cell_id=cell_id,
            reason="No OverwinterPool is configured, or this Cell's own spec is unknown "
            "(a reconciled record this process never provisioned).",
        )
    await lifecycle._pool.admit(record.cell, record.spec, scrub=scrub)
    backend = lifecycle._registry.get(record.backend)
    await backend.pause(cell_id)
    record.status = VirtualCellStatus.DORMANT
    await lifecycle._record(cell_id, "cell.overwintered")


async def _teardown(lifecycle: CellLifecycle, cell_id: CellId) -> None:
    """Destroy `cell_id`: RELEASED|READY|GRANTED|DORMANT -> DESTROYING -> DESTROYED.

    Removes the record from `lifecycle`'s table once destroyed, mirroring `CellBackend.
    list_cells`'s own contract: a destroyed Cell is gone, not archived. Also deletes this Cell's
    own snapshots from `lifecycle._snapshot_ledger`, when one is configured (roadmap step 5.10):
    a snapshot's own image/qcow2 state dies with the Cell's backend resources, so its ledger
    record should not outlive it either.

    Raises:
        CellDestroyError: The backend acknowledged the Cell but could not remove it; the record
            stays at DESTROYING, so a retry can call this again.
    """
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.DESTROYING, cell_id=cell_id)
    record.status = VirtualCellStatus.DESTROYING
    await lifecycle._record(cell_id, "cell.destroying")
    backend = lifecycle._registry.get(record.backend)
    await backend.destroy(cell_id)  # CellDestroyError propagates; record stays DESTROYING.
    if lifecycle._snapshot_ledger is not None:
        lifecycle._snapshot_ledger.delete_for_cell(cell_id)  # Roadmap 5.10: snapshots die with it.
    assert_transition(record.status, VirtualCellStatus.DESTROYED, cell_id=cell_id)
    await lifecycle._record(cell_id, "cell.destroyed")
    del lifecycle._cells[cell_id]


async def _evict_expired(lifecycle: CellLifecycle, now: datetime) -> tuple[CellId, ...]:
    """Tear down every dormant Cell past its own `dormant_until`, for the Undertaker's sweep.

    Asks the pool which ids are expired (bookkeeping only, per its own module docstring), then
    tears each one down through `teardown()` itself, so the backend call and the DORMANT ->
    DESTROYING -> DESTROYED edges happen exactly the way any other teardown does.

    Args:
        lifecycle: The CellLifecycle whose pool and table this call acts on.
        now: The reference time to compare every dormant Cell's own `dormant_until` against.

    Returns:
        Every evicted Cell's id, in the pool's own order; empty when `pool` is None or nothing has
        expired.
    """
    if lifecycle._pool is None:
        return ()
    expired_ids = await lifecycle._pool.evict_expired(now)
    for cell_id in expired_ids:
        await lifecycle.teardown(cell_id)
    return tuple(expired_ids)


def _dormant_candidates(lifecycle: CellLifecycle) -> tuple[LifecycleDormantCell, ...]:
    """Return every DORMANT Cell `lifecycle` tracks, for `queen.placement`'s own inventory.

    Hive-layer values only (module docstring); a composition root converts these into
    `hivemind.queen.placement.inventory.DormantCandidate`.
    """
    return tuple(
        LifecycleDormantCell(
            cell_id=record.cell_id,
            warden_id=record.warden_id,
            image=record.image,
            comb_shield=record.comb_shield,
        )
        for record in lifecycle._cells.values()
        if record.status is VirtualCellStatus.DORMANT
    )


def _virtual_backend_candidates(lifecycle: CellLifecycle) -> tuple[LifecycleVirtualBackend, ...]:
    """Return every registered backend with its *remaining* headroom, live.

    `headroom` is narrowed by how many Cells `lifecycle` currently tracks on that backend (every
    status but DESTROYED/FAILED, neither of which this table ever holds -- see `_teardown`/
    `_provision`'s own key invariants), matching what `queen.placement.inventory.
    VirtualBackendCandidate.capabilities.headroom`'s own docstring requires.
    """
    counts = _counts_by_backend(lifecycle._cells.values())
    candidates: list[LifecycleVirtualBackend] = []
    for name in lifecycle._registry.names():
        capabilities = lifecycle._registry.get(name).capabilities
        remaining = capabilities.headroom
        if remaining is not None:
            remaining = max(0, remaining - counts.get(name, 0))
        candidates.append(
            LifecycleVirtualBackend(
                name=name, capabilities=capabilities.model_copy(update={"headroom": remaining})
            )
        )
    return tuple(candidates)


def _from_backend_record(record: VirtualCellRecord, backend_name: str) -> LiveVirtualCell:
    """Build a LiveVirtualCell from one `CellBackend.list_cells` row, best-effort.

    Labels carry no guaranteed `comb_shield` key today (no backend stamps one), so a reconciled
    record defaults to MEADOW, the safest assumption for a Cell this process cannot otherwise
    identify: the Night Veil guard would only ever be too strict from this default, never too
    permissive.
    """
    comb_shield_label = record.labels.get("comb_shield")
    comb_shield = CombShieldLevel.MEADOW
    if comb_shield_label is not None and comb_shield_label in CombShieldLevel.__members__:
        comb_shield = CombShieldLevel[comb_shield_label]
    return LiveVirtualCell(
        cell_id=record.cell_id,
        status=record.status,
        backend=backend_name,
        image=record.image,
        comb_shield=comb_shield,
    )


def _counts_by_backend(records: Iterable[LiveVirtualCell]) -> dict[str, int]:
    """Count live records per backend name, for `_virtual_backend_candidates`'s own narrowing."""
    counts: dict[str, int] = {}
    for record in records:
        counts[record.backend] = counts.get(record.backend, 0) + 1
    return counts
