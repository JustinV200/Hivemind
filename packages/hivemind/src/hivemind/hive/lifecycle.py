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

`provision()` and `mark_ready()` are deliberately two calls, not one, even though a `CellBackend.
provision()` call already blocks until its own `ReadinessGate` sees a signed `CellReady` and the
first `CellHeartbeat` (`hivemind.hive.backends.base.CellBackend.provision`'s own contract): the
roadmap's own wording names "Warden ready" as its own milestone, and `hivemind.hive.provider.
LifecycleVirtualCellProvider` (roadmap step 5.6, this branch) needs a point after `provision()`
where it has also confirmed the Queen-side listener produced a live `WardenLink` for this Cell
before the Cell counts as truly READY for placement. `mark_ready()` is that second, separate edge.

The release decision -- overwinter or teardown -- is a policy question another implementer owns
(`hivemind.hive.overwinter.policy`, being written on this same branch right now): rather than
import that module (which would race a file this dispatch must not touch), `CellLifecycle` takes a
tiny injected callable, `DecideRelease`, defaulting to `always_teardown`, so the real policy plugs
in from the composition root once it exists. The Night Veil rule is enforced here regardless of
what the hook decides (`hivemind.hive.cell_state.can_enter_dormant`/`assert_dormant_allowed`):
codingrules section 8.7, "Night Veil lifecycle is teardown-only."

Fits into the Hive:
    Layer 3 (sources of Cells). Called by `hivemind.hive.provider.LifecycleVirtualCellProvider`
    (this branch) and, in a later step, the Undertaker (roadmap step 5.8) and `hive cells`
    commands (5.13). Calls into `hivemind.cell` (Cell, CellIdentity, CombShieldLevel),
    `hivemind.hive.backends` (BackendCapabilities, VirtualCellRecord), `hivemind.hive.cell_state`,
    `hivemind.hive.errors`, `hivemind.hive.models`, `hivemind.hive.registry`, `hivemind.pheromone`
    (CellEvent, PheromoneTrail) and waggle only.

Key invariants:
    - Every state change goes through `hivemind.hive.cell_state.assert_transition` (or
      `assert_dormant_allowed` for DORMANT) before this module's own in-memory table is updated,
      and the matching `cell.*` event is recorded in the same call, before the method returns.
    - `self._cells` is keyed by the backend's own minted `CellId`; a failed `provision()` never
      adds an entry (the backend itself already cleaned up any partial resource, per `CellBackend.
      provision`'s own contract), so there is never a lingering FAILED row to sweep.
    - A record whose `hivemind.cell.CombShieldLevel` is NIGHT_VEIL never reaches DORMANT: `release`
      never returns `"overwinter"` for one, and `overwinter()` itself refuses one directly too, so
      neither path depends on the other to keep the rule.

See Also:
    - .claude/roadmap.md step 5.6 for the edge sequence this module implements almost verbatim.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for why `provision()`
      already implies "Warden ready" at the backend level, and why `mark_ready()` still exists.
    - docs/adr/0029-overwintering-policy.md for the release-decision shape `DecideRelease` mirrors.
    - hivemind.hive.cell_state for VirtualCellStatus, TRANSITIONS and the Night Veil guard.
    - hivemind.hive.provider for LifecycleVirtualCellProvider, this module's one Queen-side caller.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from hivemind.cell import Cell, CellIdentity, CombShieldLevel
from hivemind.hive.backends.base import BackendCapabilities, VirtualCellRecord
from hivemind.hive.cell_state import (
    VirtualCellStatus,
    assert_dormant_allowed,
    assert_transition,
    can_enter_dormant,
)
from hivemind.hive.errors import CellProvisionError, UnknownCellError
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.registry import BackendRegistry
from hivemind.pheromone import CellEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, GrantId, HiveId, WardenId, new_event_id

__all__ = [
    "CellLifecycle",
    "DecideRelease",
    "LifecycleDormantCell",
    "LifecycleVirtualBackend",
    "LiveVirtualCell",
    "ReleaseDecision",
    "always_teardown",
]

# What `release()` may decide, and what a caller then asks `overwinter()`/`teardown()` to carry
# out. A plain Literal (not an Enum): this is a return value handed straight to an `if`, never
# stored or compared across a boundary, so a class the size-limited state machine already covers
# (`VirtualCellStatus`) would be the wrong tool.
ReleaseDecision = Literal["overwinter", "teardown"]

# The release policy seam: pure, no I/O, matching `hivemind.hive.overwinter.policy`'s own shape
# (a sibling implementer's module this dispatch must not import, per the module docstring).
DecideRelease = Callable[["LiveVirtualCell", VirtualCellSpec | None], ReleaseDecision]


def always_teardown(cell: LiveVirtualCell, spec: VirtualCellSpec | None) -> ReleaseDecision:
    """The default release policy: every released Virtual Cell is torn down.

    `CellLifecycle`'s own default `overwinter_policy_hook`, until a composition root injects the
    real `hivemind.hive.overwinter.policy.decide` (roadmap step 5.9). Safe on its own: never
    overwintering is always a legal outcome, unlike the reverse.

    Args:
        cell: The released Cell's own lifecycle record.
        spec: The `VirtualCellSpec` it was provisioned from, or None for a Cell this process only
            ever learned about through `reconcile()` (see `LiveVirtualCell.spec`'s own docstring).

    Returns:
        `"teardown"`, always.
    """
    del cell, spec  # Unused: the module docstring's own documented default.
    return "teardown"


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
            lists no store for it); `DecideRelease` implementations must handle a None spec.
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
    type once it already holds both (see this dispatch's own report for which layer does that).

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
        overwinter_policy_hook: DecideRelease = always_teardown,
    ) -> None:
        """Build a CellLifecycle with an empty table; call `reconcile()` before relying on it.

        Args:
            registry: Every registered `CellBackend`, looked up by name on every edge.
            trail: Where every `cell.*` event this lifecycle drives lands.
            clock: Source of every minted event id and timestamp.
            identity: The Hive, node and actor this lifecycle stamps on every trail event.
            overwinter_policy_hook: Decides `"overwinter"` or `"teardown"` for a released Cell;
                defaults to `always_teardown` until a composition root injects the real
                `hivemind.hive.overwinter.policy` (roadmap step 5.9, another implementer's file).
        """
        self._registry = registry
        self._trail = trail
        self._clock = clock
        self._identity = identity
        self._decide_release = overwinter_policy_hook
        self._cells: dict[CellId, LiveVirtualCell] = {}

    def status_of(self, cell_id: CellId) -> VirtualCellStatus | None:
        """Return `cell_id`'s current status, or None if this lifecycle has no record of it."""
        record = self._cells.get(cell_id)
        return record.status if record is not None else None

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

    async def grant(self, cell_id: CellId, grant_id: GrantId) -> None:
        """Move `cell_id` READY -> GRANTED. See `_grant`."""
        await _grant(self, cell_id, grant_id)

    async def release(self, cell_id: CellId) -> ReleaseDecision:
        """Move `cell_id` GRANTED -> RELEASED, then decide overwinter/teardown. See `_release`."""
        return await _release(self, cell_id)

    async def overwinter(self, cell_id: CellId) -> None:
        """Move `cell_id` RELEASED -> DORMANT. See `_overwinter`."""
        await _overwinter(self, cell_id)

    async def teardown(self, cell_id: CellId) -> None:
        """Destroy `cell_id`. See `_teardown`."""
        await _teardown(self, cell_id)

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

    The caller (`hivemind.hive.provider.LifecycleVirtualCellProvider`) is responsible for calling
    `backend.resume(cell_id)` and waiting for the resumed Cell's fresh heartbeat before this; this
    only records the lifecycle's own edge once that has happened.
    """
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.READY, cell_id=cell_id)
    record.status = VirtualCellStatus.READY
    if warden_id is not None:
        record.warden_id = warden_id
    await lifecycle._record(cell_id, "cell.resumed", warden_id=warden_id)


async def _grant(lifecycle: CellLifecycle, cell_id: CellId, grant_id: GrantId) -> None:
    """Move `cell_id` READY -> GRANTED: placement handed it to a task."""
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.GRANTED, cell_id=cell_id)
    record.status = VirtualCellStatus.GRANTED
    record.grant_id = grant_id
    await lifecycle._record(cell_id, "cell.granted", grant_id=grant_id)


async def _release(lifecycle: CellLifecycle, cell_id: CellId) -> ReleaseDecision:
    """Move `cell_id` GRANTED -> RELEASED, then decide overwinter or teardown.

    Records `cell.virtual_released` (a distinct kind from `cell.released`, which already means "a
    Real Cell lease closed and the device restored" -- a different fact about a different kind of
    Cell; see this dispatch's own report for the naming decision). Does not itself perform the
    decided edge: the caller calls `overwinter()` or `teardown()` next.
    """
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.RELEASED, cell_id=cell_id)
    record.status = VirtualCellStatus.RELEASED
    record.grant_id = None
    await lifecycle._record(cell_id, "cell.virtual_released")
    # The Night Veil rule is enforced here, ahead of the hook, so a released Night Veil Cell is
    # never even offered to overwinter_policy_hook ("regardless of what the hook decides").
    if not can_enter_dormant(record.comb_shield):
        return "teardown"
    return lifecycle._decide_release(record, record.spec)


async def _overwinter(lifecycle: CellLifecycle, cell_id: CellId) -> None:
    """Move `cell_id` RELEASED -> DORMANT and pause it on its backend."""
    record = lifecycle._require(cell_id)
    assert_transition(record.status, VirtualCellStatus.DORMANT, cell_id=cell_id)
    # A second guard, independent of release()'s own decision (module docstring): even a direct
    # call to overwinter() can never move a NIGHT_VEIL Cell to DORMANT.
    assert_dormant_allowed(record.comb_shield, cell_id=cell_id)
    backend = lifecycle._registry.get(record.backend)
    await backend.pause(cell_id)
    record.status = VirtualCellStatus.DORMANT
    await lifecycle._record(cell_id, "cell.overwintered")


async def _teardown(lifecycle: CellLifecycle, cell_id: CellId) -> None:
    """Destroy `cell_id`: RELEASED|READY|GRANTED|DORMANT -> DESTROYING -> DESTROYED.

    Removes the record from `lifecycle`'s table once destroyed, mirroring `CellBackend.
    list_cells`'s own contract: a destroyed Cell is gone, not archived.

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
    assert_transition(record.status, VirtualCellStatus.DESTROYED, cell_id=cell_id)
    await lifecycle._record(cell_id, "cell.destroyed")
    del lifecycle._cells[cell_id]


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
