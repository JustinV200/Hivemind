"""Define Undertaker: the cleanup role that destroys Virtual Cells and releases Real Cell leases.

Roadmap step 5.8: the Undertaker is the Worker (`hivemind.workers.base.Worker`) whose whole job is
tearing down what a task no longer needs -- a Virtual Cell it provisioned, or the lease it borrowed
on a Real Cell -- idempotently, with retries, revoking every grant and (for a Virtual Cell) retiring
its Cell Wax and marking its Leavings ledger rows removed. It is autopilot-only, like
`hivemind.workers.roles.house_bee.HouseBee`: it never awaits a model and never imports
`hivemind.llm`, because tearing down infrastructure needs no judgement call. `destroy_virtual` and
`release_real` are its two real operations, each idempotent on its own (an unknown Cell id is
already gone; `hivemind.cell.lease.RealCellLease.release()` is already idempotent for its own
happy path). Every backend and grant-revocation call retries with exponential backoff, so a flaky
backend gets several tries before the caller (a sweep, or a future `hive cells destroy`/`release`
CLI command) has to give up; `release_real` calls `lease.release()` itself exactly once and does
not retry it (see that method's own docstring for why retrying it is unsafe). `run` is the
`Worker`-protocol adapter around `destroy_virtual`: codingrules section 8.7 names the Undertaker as
one of exactly two places allowed to branch on `hivemind.cell.CellKind` (placement is the other),
because deciding "destroy this Virtual Cell" versus "this Real Cell's lease is released by whoever
holds it" is precisely that branch.

Three collaborators are injected as small Protocols rather than imported directly, because the
Undertaker sits at Layer 4 (`hivemind.workers`) and each one's real implementation sits above it:
`GrantRevoker` wraps `hivemind.queen.forage.grants.revoke` (Layer 6); `WaxRetirer` is a tiny seam
for the real Cell-Wax-to-Honey hand-off phase 7's Honey Store adds; `LeavingsRemover` wraps the
Leavings ledger (`hivemind.cell.leavings.LeavingsStore`, built on another branch, not yet merged --
see `LeavingsRemover`'s own docstring for the adapter shape that will wrap it).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.undertaker`. Constructed by
    whichever composition root spawns an Undertaker (a Warden, or `hivemind.workers.roles.
    undertaker.sweep.sweep_orphans` directly, since a sweep is Queen-side maintenance with no
    per-task assignment) and run at most once per attempt by `hivemind.workers.runtime.
    WorkerRuntime`, mirroring `hivemind.workers.roles.house_bee.HouseBee`. Calls into
    `hivemind.cell` (CellIdentity, CellKind, HoneyClearance, RealCellLease, LeaseReleaseReport),
    `hivemind.hive` (CellBackend, HiveError), `hivemind.memory` (Handoff), `hivemind.pheromone`
    (CellEvent, PheromoneTrail), `hivemind.workers.base`, `hivemind.workers.context` and waggle
    only. Never imports `hivemind.llm` (module docstring).

Key invariants:
    - `destroy_virtual` never raises for an unknown Cell id: `CellBackend.destroy` is itself
      idempotent (its own contract), so calling it twice, or after the fact, is always safe.
    - `release_real` on the same lease twice is safe only when the first call already succeeded
      (`RealCellLease.release`'s own idempotent happy path); a lease whose first `release()` call
      failed mid-flight cannot be released again through this or any other caller (see that
      method's own docstring).
    - Every retried call gives up after `RetryPolicy.max_attempts` and re-raises the last error;
      nothing is ever retried forever.
    - `run` never marks a task SUCCEEDED (codingrules section 8.7): it always returns
      `claimed=True, handoff=None`, the same shape `HouseBee.run` uses for the same reason.

See Also:
    - .claude/codingrules.md section 8.7 for the CellKind branch this role is one of two allowed
      callers of, and for "Owned versus leased" (destroy versus release).
    - .claude/roadmap.md step 5.8 for this role's spec verbatim.
    - hivemind.workers.roles.undertaker.sweep for sweep_orphans, the Queen-startup caller of
      destroy_virtual/release_real for orphans the trail and backend labels reveal.
    - hivemind.workers.roles.house_bee.role for HouseBee, the autopilot-only shape this mirrors.
    - hivemind.queen.forage.grants for revoke, the real implementation a GrantRevoker wraps.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeVar

from pydantic import JsonValue

from hivemind.cell import CellIdentity, CellKind, HoneyClearance, LeaseReleaseReport, RealCellLease
from hivemind.hive import CellBackend, HiveError
from hivemind.memory import Handoff
from hivemind.pheromone import CellEvent, PheromoneTrail
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock
from waggle.ids import CellId, EventId, new_event_id
from waggle.messages.task import TaskAssign, WorkerRole

# Retry constants (exponential backoff, mirrors hivemind.queen.cluster.health.ClusterBackoff's own
# reasoning: doubling is cheap to reason about and the cap keeps a stuck teardown from waiting
# minutes between attempts).
DEFAULT_MAX_ATTEMPTS = 5  # A flaky backend or releaser gets several tries before this gives up.
DEFAULT_INITIAL_BACKOFF_S = 1.0  # First retry waits one second.
DEFAULT_BACKOFF_FACTOR = 2.0  # Doubling each attempt after the first.
DEFAULT_MAX_BACKOFF_S = 30.0  # Cap: never wait longer than half a minute between attempts.

_T = TypeVar("_T")

__all__ = [
    "DEFAULT_BACKOFF_FACTOR",
    "DEFAULT_INITIAL_BACKOFF_S",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_BACKOFF_S",
    "GrantRevoker",
    "LeavingsRemover",
    "NullLeavingsRemover",
    "NullWaxRetirer",
    "RetryPolicy",
    "Undertaker",
    "UndertakerDeps",
    "WaxRetirer",
]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times, and how long to wait between, the Undertaker retries a failed step."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    initial_backoff_s: float = DEFAULT_INITIAL_BACKOFF_S
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR
    max_backoff_s: float = DEFAULT_MAX_BACKOFF_S


class GrantRevoker(Protocol):
    """Revoke every live Forage grant tied to one Cell, once it is destroyed or released.

    Wraps `hivemind.queen.forage.grants.revoke` (Layer 6); the Undertaker (Layer 4) never imports
    that module directly (codingrules section 4's layer table), so a composition root that already
    holds a `hivemind.queen.forage.ledger.ForageLedger` and `hivemind.queen.deps.QueenDeps` builds
    the real implementation and injects it here.
    """

    async def revoke_for_cell(self, cell_id: CellId, reason: str) -> int:
        """Revoke every live grant this Cell's own Warden holds.

        Idempotent: a Cell with no live grants left (already revoked, or never granted) returns 0
        rather than raising, so a retried or repeated call is always safe.

        Args:
            cell_id: The Cell whose grants are revoked.
            reason: Why, folded into the trail event the real implementation records.

        Returns:
            How many grants were actually revoked by this call; 0 when none were live.
        """
        ...


class WaxRetirer(Protocol):
    """Retire every Cell Wax note for a destroyed Virtual Cell.

    A tiny seam: the real ripening hand-off (Cell Wax into Honey) is phase 7's Honey Store, not yet
    built. Until then, the real implementation only needs to move each note out of hot state; a
    fake used by tests and `hive doctor` just records what it was asked to retire.
    """

    async def retire_wax(self, cell_id: CellId, at: datetime) -> int:
        """Retire every still-live Cell Wax note naming `cell_id`.

        Idempotent: a Cell with nothing left to retire returns 0.

        Args:
            cell_id: The destroyed Cell whose notes are retired.
            at: When retirement happened (an injected Clock reading).

        Returns:
            How many notes were actually retired by this call.
        """
        ...


class LeavingsRemover(Protocol):
    """Mark a destroyed Virtual Cell's Leavings ledger rows removed: the paths died with the Cell.

    The Leavings ledger (roadmap step 5.0a) lives on another branch as `hivemind.cell.leavings.
    LeavingsStore`, not yet merged; this Protocol is deliberately defined here rather than importing
    that module, so this dispatch never depends on unmerged work. Once it lands, the real adapter is
    a ten-line wrap around `LeavingsStore.list_leavings`/`.mark_removed`:

        class LeavingsStoreRemover:
            def __init__(self, store: LeavingsStore, event: str = "cell_destroyed") -> None:
                self._store = store
                self._event = event

            async def mark_cell_removed(self, cell_id: CellId, at: datetime) -> int:
                rows = await self._store.list_leavings(cell_id, include_removed=False)
                for row in rows:
                    await self._store.mark_removed(cell_id, row.path, at, self._event)
                return len(rows)

    `release_real` never calls this (roadmap step 5.8: "releasing a Real Cell never touches a
    ledgered path"): only `destroy_virtual` does, since a Virtual Cell's paths die with the Cell
    while a Real Cell's paths live on after its lease ends.
    """

    async def mark_cell_removed(self, cell_id: CellId, at: datetime) -> int:
        """Mark every not-yet-removed Leavings row for `cell_id` removed.

        Idempotent: a Cell with nothing left to mark returns 0.

        Args:
            cell_id: The destroyed Virtual Cell.
            at: When the rows are marked removed (an injected Clock reading).

        Returns:
            How many rows were actually marked removed by this call.
        """
        ...


class NullLeavingsRemover:
    """A LeavingsRemover that marks nothing; the default until the Leavings ledger is wired in."""

    async def mark_cell_removed(self, cell_id: CellId, at: datetime) -> int:
        """Mark nothing removed and report 0; see `LeavingsRemover.mark_cell_removed`."""
        del cell_id, at  # Nothing to mark: the Leavings ledger is not wired in yet.
        return 0


class NullWaxRetirer:
    """A WaxRetirer that retires nothing; for a caller with no Cell Wax store of its own to hand.

    Added by roadmap step 5.13 (`hive cells destroy`/`abscond`): those commands build a plain
    `Undertaker` offline, over whatever `GrantRevoker` and `LeavingsRemover` they can wire for
    real, but have no live Cell Wax store to retire against -- the same documented gap
    `NullLeavingsRemover` already covers for the Leavings ledger, mirrored here for symmetry.
    """

    async def retire_wax(self, cell_id: CellId, at: datetime) -> int:
        """Retire nothing and report 0; see `WaxRetirer.retire_wax`."""
        del cell_id, at  # Nothing to retire: this caller has no Cell Wax store wired in.
        return 0


@dataclass(frozen=True, slots=True)
class UndertakerDeps:
    """Every collaborator one Undertaker needs.

    Bundled so its own `__init__` stays within codingrules 5.1's parameter limit (mirrors
    `hivemind.workers.roles.house_bee.sweep.SweepDeps`).

    Attributes:
        backend: Where `destroy_virtual` actually destroys a Virtual Cell.
        grant_revoker: Revokes a Cell's live Forage grants.
        wax_retirer: Retires a destroyed Virtual Cell's Cell Wax notes.
        leavings_remover: Marks a destroyed Virtual Cell's Leavings rows removed.
        trail: Where `cell.destroyed`/`cell.released` events land.
        clock: Source of every timestamp this role reads or records, and every retry's own sleep.
        identity: The Hive, node and actor this role stamps on every trail event it writes.
    """

    backend: CellBackend
    grant_revoker: GrantRevoker
    wax_retirer: WaxRetirer
    leavings_remover: LeavingsRemover
    trail: PheromoneTrail
    clock: Clock
    identity: CellIdentity


class Undertaker:
    """The cleanup role: destroys Virtual Cells, releases Real Cell leases, both idempotently.

    `role` is a fixed property (codingrules section 8.1: implements `hivemind.workers.base.Worker`
    structurally), matching `hivemind.workers.roles.house_bee.HouseBee`'s own shape.
    """

    def __init__(self, deps: UndertakerDeps, retry: RetryPolicy | None = None) -> None:
        """Build an Undertaker over its injected collaborators.

        Args:
            deps: Every collaborator this role needs.
            retry: The backoff schedule every retried step follows; `RetryPolicy()`'s own defaults
                when omitted.
        """
        self._deps = deps
        self._retry = retry if retry is not None else RetryPolicy()

    @property
    def role(self) -> WorkerRole:
        """This role is always UNDERTAKER."""
        return WorkerRole.UNDERTAKER

    async def run(
        self, ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        """Clean up `ctx.cell`.

        Destroys it if Virtual; notes that a Real Cell's lease is not this attempt's to release.

        Args:
            ctx: Everything this attempt may use; only `cell` and `clock`/`trail`/`identity`
                (already carried by this role's own `UndertakerDeps`) are read.
            assignment: What was assigned; only `clearance` is read, as this attempt's own
                allowance, matching `HouseBee.run`.
            resume_from: Accepted for protocol conformance and ignored: cleanup is not resumable
                work, the same reasoning `HouseBee.run` gives for a sweep.

        Returns:
            A `claimed=True` WorkerOutcome; an Undertaker never marks a task SUCCEEDED.
        """
        del resume_from  # Cleanup is not resumable work (module docstring; mirrors HouseBee.run).
        clearance = HoneyClearance.from_wire(assignment.clearance)
        # codingrules 8.7: CellKind matters to exactly two callers, placement and the Undertaker;
        # this is that second, allowed branch (scripts/check_no_kind_branches.py allowlists this
        # package's path), deciding whether ctx.cell is destroyed or left for its lease holder.
        if ctx.cell.kind is CellKind.VIRTUAL:
            await self.destroy_virtual(ctx.cell.id)
            summary = f"Destroyed Virtual Cell {ctx.cell.id}."
        else:
            # A Worker's own WorkerContext exposes only a read-only LeaseView
            # (hivemind.supervision.capping.LeaseView) for its Real Cell, never the mutable
            # RealCellLease `release_real` needs: that object is owned by whichever Warden holds
            # the lease, and releases it directly (RealCellLease.release() is already idempotent).
            # An Undertaker attempt spawned on a Real Cell has nothing further of its own to do.
            summary = f"Cell {ctx.cell.id} is Real; its own lease holder releases it directly."
        return WorkerOutcome(
            summary=summary,
            clearance=clearance,
            artifacts=(),
            claimed=True,
            handoff=None,
            spend_usd=0.0,
        )

    async def destroy_virtual(self, cell_id: CellId) -> EventId:
        """Destroy a Virtual Cell, revoke its grants, retire its Cell Wax, mark its Leavings gone.

        Idempotent end to end: an unknown or already-destroyed `cell_id` still runs every step, and
        every injected collaborator's own contract (module docstring) makes that safe -- a Cell
        with nothing left to revoke, retire or mark simply returns 0 from each.

        Args:
            cell_id: The Virtual Cell to destroy.

        Returns:
            The id of the `cell.destroyed` trail event this call records -- `hive cells destroy`
            (roadmap step 5.13) prints it as the operator-facing receipt of what happened.

        Raises:
            hivemind.hive.errors.CellDestroyError: The backend acknowledged the Cell exists but
                could not remove it, after `RetryPolicy.max_attempts` tries.
        """
        await self._retrying(lambda: self._deps.backend.destroy(cell_id), HiveError)
        at = self._deps.clock.now()
        revoked = await self._retrying(
            lambda: self._deps.grant_revoker.revoke_for_cell(cell_id, "Virtual Cell destroyed."),
            HiveError,
        )
        retired = await self._retrying(
            lambda: self._deps.wax_retirer.retire_wax(cell_id, at), HiveError
        )
        removed = await self._retrying(
            lambda: self._deps.leavings_remover.mark_cell_removed(cell_id, at), HiveError
        )
        return await self._record(
            "cell.destroyed",
            cell_id,
            {"grants_revoked": revoked, "wax_retired": retired, "leavings_removed": removed},
        )

    async def release_real(self, lease: RealCellLease) -> LeaseReleaseReport:
        """Release a Real Cell lease and revoke its grant; never touches a ledgered path or wax.

        `lease.release()` already writes its own `cell.released` trail event and is already
        idempotent for the happy path: once it has produced a report, every later call returns
        that same report without touching the releaser again (`RealCellLease.release`'s own
        docstring). It is deliberately called only once here, not through `_retrying`: `release()`
        moves `lease.state` to `RELEASING` before it ever awaits the injected `LeaseReleaser`, and
        that edge has no way back (`hivemind.cell.lease_state.TRANSITIONS`), so a `LeaseReleaser`
        failure leaves the lease stuck at `RELEASING` -- a second `release()` call would raise
        `InvalidLeaseTransitionError` (`RELEASING -> RELEASING` is not a legal edge) instead of
        trying the releaser again. Only the grant revocation that follows is retried.

        Args:
            lease: The lease to release.

        Returns:
            The same LeaseReleaseReport `lease.release()` produces.

        Raises:
            hivemind.cell.errors.InvalidLeaseTransitionError: A previous `release()` call on this
                same lease already failed after moving it to `RELEASING` (module docstring); the
                lease itself cannot be retried, only a fresh lease on the same Cell can.
        """
        report = await lease.release()
        await self._retrying(
            lambda: self._deps.grant_revoker.revoke_for_cell(
                lease.cell_id, "Real Cell lease released."
            ),
            HiveError,
        )
        return report

    async def _retrying(
        self,
        op: Callable[[], Awaitable[_T]],
        retry_on: type[BaseException] | tuple[type[BaseException], ...],
    ) -> _T:
        """Call `op`, retrying on `retry_on` with backoff, up to `RetryPolicy.max_attempts` times.

        Args:
            op: The zero-argument coroutine factory to call.
            retry_on: The exception type(s) worth retrying; anything else propagates at once.

        Returns:
            Whatever `op()` eventually returns.

        Raises:
            The last raised `retry_on` exception, once `RetryPolicy.max_attempts` is reached.
        """
        attempt = 0
        while True:
            try:
                return await op()
            except retry_on:
                attempt += 1
                if attempt >= self._retry.max_attempts:
                    raise  # Out of retries: the caller (a sweep, a CLI command) decides what next.
                delay = min(
                    self._retry.initial_backoff_s * self._retry.backoff_factor ** (attempt - 1),
                    self._retry.max_backoff_s,
                )
                # External await: a bounded pause between retries, driven by the injected Clock so
                # a test controls it exactly (waggle.clock.FakeClock.sleep never really waits).
                await self._deps.clock.sleep(delay)

    async def _record(
        self, kind: str, cell_id: CellId, payload: Mapping[str, JsonValue]
    ) -> EventId:
        """Build and record a CellEvent for `cell_id`, stamped with this role's own identity.

        Returns:
            The recorded event's own id, so a caller that needs a receipt (`destroy_virtual`) can
            hand it back without re-querying the trail.
        """
        event = CellEvent(
            id=new_event_id(self._deps.clock),
            hive_id=self._deps.identity.hive_id,
            node_id=self._deps.identity.node_id,
            at=self._deps.clock.now(),
            actor=self._deps.identity.actor,
            kind=kind,
            subject_id=cell_id,
            payload=dict(payload),
        )
        await self._deps.trail.record(event)
        return event.id
