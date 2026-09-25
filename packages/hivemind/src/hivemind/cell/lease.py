"""Define LeaseRequest, LeaseReleaseReport, RestoreRecord and RealCellLease: a Real Cell's tenancy.

A `RealCellLease` is one Warden's tenancy on a Real Cell (an existing device the Hive borrows and
leaves exactly as found): its own scratch directory, the process ids its session started, the
paths it touched outside scratch, the `RestoreRecord`s a Capping proposal wrote outside scratch
(roadmap step 3.17), and the `LeaseState` (`hivemind.cell.lease_state`) it moves through from
request to release. `LeaseRequest` is what a caller asks a `RealCellSource` for;
`LeaseReleaseReport` is what `release()` returns, saying whether the device was left as found;
`RestoreRecord` is one path outside scratch and what must be put back there. `RealCellLease` is
the one class in this package that owns mutable bookkeeping in place (codingrules section 8.5
requires that to be documented, so it is, here): its frozen facts never change once opened, but
`state`, the started-process list, the touched-path list and the restore-record list all grow or
change over the lease's life. `release()` never does the actual killing, path restoration or
replay itself; it delegates that to an injected `LeaseReleaser`, so the identical bookkeeping,
idempotence and trail-writing logic serves both `hivemind.cell.fake.FakeCellSource` and
`hivemind.cell.local.HiveStandSource` (phase 3 step 3.11) without either reimplementing it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Constructed by a RealCellSource's
    `lease()` (`hivemind.cell.source`); read and released by a Warden (Layer 5) and the
    Undertaker (`hivemind.workers.roles.undertaker`, a later phase). Calls into
    hivemind.cell.lease_state, hivemind.cell.leavings.model (ApprovedBy, roadmap step 5.0a),
    hivemind.cell.tiers and hivemind.pheromone (CellEvent) only; never hivemind.guard (codingrules
    section 4: nothing at Layer 2 or below imports guard for an enum), so this module holds no
    CapabilitySet -- a Warden builds one from `access_level` and `scratch_root` via
    `hivemind.guard.warden_set` (its Guard policy role default, narrowed to the access level by
    `hivemind.guard.access`) once it has both a Cell and a Layer-2 lease in hand.

Key invariants:
    - `RealCellLease.__init__` leaves `state` at `LeaseState.REQUESTED`; only `open()` transitions
      it to OPEN, and only `release()` transitions it onward, both through
      `hivemind.cell.lease_state.assert_transition`.
    - `open()` and `release()` each write their trail event (`cell.leased`, `cell.released`) in
      the same call that changes `state`, with `subject_id` set to `cell_id`.
    - `release()` is idempotent: once it has produced a report, every later call returns that same
      report without calling the injected LeaseReleaser or the trail again.
    - `release()` never leaves a lease stuck in RELEASING: a delegate that raises moves the lease
      to ORPHANED and re-raises, so a later call is legal (`hivemind.cell.lease_state`'s
      `RELEASING -> ORPHANED` and `ORPHANED -> RELEASING` edges) and `cell.released` is written at
      most once, only by the call that actually succeeds.
    - `is_path_allowed` and `note_touched_path` both resolve `..` and symlinks with
      `Path.resolve(strict=False)` before comparing, so neither can be used to sneak outside
      scratch undetected.
    - `note_restore_path` never records a path that resolves inside `scratch_root`: scratch is
      removed wholesale on release, so there is nothing individual to restore there.
    - `note_restore_path`'s `persist`, `approved_by` and `reason` (roadmap step 5.0a) only decide
      what `release()` does with the record later; this method never decides *whether* to persist
      -- that is a later step's job (5.0c/5.0d) -- it only carries the decision a caller already
      made, validated together by `RestoreRecord`'s own validator.

See Also:
    - .claude/codingrules.md section 8.5 for the "a class documents its own mutable state" rule
      this module follows for RealCellLease.
    - .claude/codingrules.md section 8.7 for "Owned versus leased" and what release() restores.
    - .claude/codingrules.md Appendix C, "Lease (Real Cell)" row, for the state machine this
      module's `open`/`release` drive.
    - hivemind.cell.lease_state for LeaseState and the transition table.
    - hivemind.cell.source for RealCellSource (this module's caller) and CellIdentity (the
      identity `open`/`release` stamp on every event; imported here only for type checking, to
      avoid a runtime import cycle between the two modules).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from hivemind.cell.lease_state import LeaseState, assert_transition
from hivemind.cell.leavings.model import ApprovedBy
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.pheromone import CellEvent, PheromoneTrail
from waggle.clock import Clock
from waggle.ids import CellId, LeaseId, TaskId, WardenId, new_event_id
from waggle.messages.base import (
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    CellIdField,
    TaskIdField,
    WardenIdField,
)

if TYPE_CHECKING:
    # Only for annotations (this module carries `from __future__ import annotations`): importing
    # CellIdentity at runtime would import hivemind.cell.source, which imports this module for
    # RealCellLease -- a cycle. Neither open() nor release() needs to construct a CellIdentity,
    # only read the one a caller already built, so a type-checking-only import is enough.
    from hivemind.cell.source import CellIdentity

MAX_ALLOWED_PATHS = 64  # Mirrors waggle.messages.cell.leases.MAX_ALLOWED_PATHS: a generous handful.

__all__ = [
    "MAX_ALLOWED_PATHS",
    "LeaseFacts",
    "LeaseReleaseReport",
    "LeaseReleaser",
    "LeaseRequest",
    "RealCellLease",
    "RestoreRecord",
]


class LeaseRequest(BaseModel):
    """What a caller asks a RealCellSource for: which Cell, held by whom, at what access."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_id: CellIdField = Field(description="The Cell to lease.")
    holder: WardenIdField = Field(description="The Warden that will own the resulting lease.")
    task_id: TaskIdField | None = Field(
        default=None, description="The task the lease is for; None for internal use."
    )
    access_level: AccessLevel = Field(description="The most the holder needs.")
    allowed_paths: tuple[Path, ...] = Field(
        default=(), max_length=MAX_ALLOWED_PATHS, description="Paths outside scratch to allow."
    )


class LeaseReleaseReport(BaseModel):
    """What `RealCellLease.release()` returns: whether the device was left as found."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    killed_processes: int = Field(
        ge=0, description="Processes the lease started that were terminated on release."
    )
    residual_paths: tuple[Path, ...] = Field(
        default=(), description="Paths outside scratch that could not be restored."
    )
    left_paths: tuple[Path, ...] = Field(
        default=(),
        description="Paths outside scratch a Leavings ledger record now covers, left in place on "
        "purpose (roadmap step 5.0a); never in residual_paths, since leaving them is not a "
        "failure.",
    )
    is_restored: bool = Field(
        description="True when the scratch directory is gone and every touched path restored or "
        "ledgered as a Leaving; a path in left_paths never counts against this."
    )


class RestoreRecord(BaseModel):
    """One path a lease wrote outside scratch, and what release() must put back.

    Recorded by `RealCellLease.note_restore_path` whenever a Capping proposal (roadmap step
    3.17) applies an `outside_scratch_write` outside a lease's scratch directory; a path inside
    scratch is never recorded here, since scratch is removed wholesale on release.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path = Field(
        description="The resolved absolute path outside scratch that the Hive wrote."
    )
    prior: bytes | None = Field(
        description="The bytes at `path` before the Hive wrote it; None when `path` did not "
        "exist yet, in which case release() deletes it rather than restoring content."
    )
    persist: bool = Field(
        default=False,
        description="True once a caller has decided this path should stay rather than be "
        "restored (roadmap step 5.0a; the decision itself is a later step's job, 5.0c/5.0d); "
        "release() skips a record with persist=True rather than undoing it, and instead writes "
        "it to the Leavings ledger as a Leaving.",
    )
    approved_by: ApprovedBy | None = Field(
        default=None,
        description="Who allowed persist=True: POLICY or HUMAN. Required together with `reason` "
        "exactly when persist is True; the ledger row release() writes needs both.",
    )
    reason: str | None = Field(
        default=None,
        max_length=MAX_REASON_CHARS,
        description="Why this path was allowed to stay, one line. Required together with "
        "`approved_by` exactly when persist is True.",
    )

    @model_validator(mode="after")
    def _persist_requires_approval(self) -> RestoreRecord:
        """Require approved_by and reason together exactly when persist is True.

        `release()` builds a Leavings ledger row straight from a persisted record (roadmap step
        5.0a), and that row's own `approved_by`/`reason` fields are not optional -- catching a
        caller that set `persist=True` without them here, at record time, is cheaper than a
        confusing failure deep inside `release()`.
        """
        if self.persist and (self.approved_by is None or not self.reason):
            raise ValueError(
                "RestoreRecord(persist=True) requires both approved_by and a non-empty reason."
            )
        if not self.persist and (self.approved_by is not None or self.reason is not None):
            raise ValueError(
                "RestoreRecord(persist=False) must not set approved_by or reason: neither means "
                "anything without persist=True."
            )
        return self


class LeaseReleaser(Protocol):
    """Perform the actual kill-and-restore side effects `RealCellLease.release()` delegates to.

    The same `RealCellLease` class serves a fake and the Hive Stand only because the platform-
    specific work (killing processes, removing a scratch directory) is injected rather than
    written into `release()` itself. Implementations: `hivemind.cell.fake.FakeLeaseReleaser`,
    `hivemind.cell.local.HiveStandLeaseReleaser` (phase 3 step 3.11).
    """

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        """Kill what `lease` started and restore what it touched outside scratch.

        Args:
            lease: The lease being released, already transitioned to RELEASING.

        Returns:
            The outcome: how many processes were killed, which paths (if any) could not be
            restored, and whether the device is left as found.
        """
        ...


@dataclass(frozen=True, slots=True)
class LeaseFacts:
    """The facts about a lease that never change once it is opened.

    Computed once by a RealCellSource's `lease()` and handed to `RealCellLease.__init__`; kept as
    its own dataclass (rather than as individual `RealCellLease.__init__` parameters) so that
    constructor stays within codingrules 5.1's five-parameter limit.
    """

    id: LeaseId  # Minted by the source that opens this lease.
    cell_id: CellId  # The Cell leased.
    holder: WardenId  # The Warden that owns the tenancy.
    task_id: TaskId | None  # The task it serves, if any.
    scratch_root: Path  # This lease's own scratch directory.
    access_level: AccessLevel  # The level actually granted.
    comb_shield: CombShieldLevel  # The Cell's tier the task inherits.
    allowed_paths: tuple[Path, ...]  # Paths outside scratch this lease may touch.


class RealCellLease:
    """One Warden's tenancy on a Real Cell: scratch root, started processes, touched paths.

    Mutable state, owned by this class alone (codingrules 8.5): `state` moves along
    `hivemind.cell.lease_state.TRANSITIONS`, one trail event per method that changes it.
    """

    def __init__(
        self,
        facts: LeaseFacts,
        trail: PheromoneTrail,
        clock: Clock,
        identity: CellIdentity,
        releaser: LeaseReleaser,
    ) -> None:
        """Build a lease in REQUESTED state; call `open()` next to make it usable.

        Args:
            facts: This lease's frozen facts, computed by the source opening it.
            trail: Where cell.leased/.released/.touched_outside_scratch events land.
            clock: Source of every minted event id and timestamp.
            identity: The Hive, node and actor stamped on every trail event.
            releaser: The kill-and-restore work `release()` delegates to.
        """
        self.id = facts.id
        self.cell_id = facts.cell_id
        self.holder = facts.holder
        self.task_id = facts.task_id
        self.scratch_root = facts.scratch_root
        self.access_level = facts.access_level
        self.comb_shield = facts.comb_shield
        self.allowed_paths = facts.allowed_paths
        self.state = LeaseState.REQUESTED
        self._trail = trail
        self._clock = clock
        self._identity = identity
        self._releaser = releaser
        self._started_pids: list[int] = []
        self._touched_paths: list[Path] = []
        self._restore_records: list[RestoreRecord] = []
        self._release_report: LeaseReleaseReport | None = None

    @property
    def started_pids(self) -> tuple[int, ...]:
        """Every process id `note_started_process` recorded; read by the LeaseReleaser to kill."""
        return tuple(self._started_pids)

    @property
    def touched_paths(self) -> tuple[Path, ...]:
        """Every resolved path `note_touched_path` has recorded, in the order they were touched."""
        return tuple(self._touched_paths)

    @property
    def restore_records(self) -> tuple[RestoreRecord, ...]:
        """Every RestoreRecord `note_restore_path` recorded; the LeaseReleaser replays them."""
        return tuple(self._restore_records)

    async def open(self) -> None:
        """Transition this lease from REQUESTED to OPEN and record cell.leased.

        Raises:
            InvalidLeaseTransitionError: This lease is not in REQUESTED state (called twice).
        """
        assert_transition(self.state, LeaseState.OPEN, lease_id=self.id)
        self.state = LeaseState.OPEN
        await self._record(
            "cell.leased",
            {
                "lease_id": self.id,
                "holder": self.holder,
                "task_id": self.task_id,
                "access_level": self.access_level.value,
                "comb_shield": self.comb_shield.value,
            },
        )

    def note_started_process(self, pid: int) -> None:
        """Record `pid`, a process this lease's session started, so `release()` can kill it."""
        self._started_pids.append(pid)

    async def note_touched_path(self, path: Path) -> None:
        """Record that this lease's session touched `path`, resolved first.

        Writes `cell.touched_outside_scratch` when outside `scratch_root` (codingrules 12).
        """
        # ASYNC240: Path.resolve() lives in a plain, non-async helper, never inline here.
        resolved, within_scratch = _resolve_touched(path, self.scratch_root)
        self._touched_paths.append(resolved)
        if not within_scratch:
            await self._record(
                "cell.touched_outside_scratch",
                {"lease_id": self.id, "path": str(resolved)[:MAX_PATH_CHARS]},
            )

    def note_restore_path(
        self,
        path: Path,
        prior: bytes | None,
        *,
        persist: bool = False,
        approved_by: ApprovedBy | None = None,
        reason: str | None = None,
    ) -> None:
        """Record what `release()` must put back at `path` (resolved first) -- or leave in place.

        Args:
            path: The path written outside scratch, relative or absolute.
            prior: Bytes at `path` before this write, or None if it did not exist yet.
            persist: True once a caller *decided* this path should stay (`RestoreRecord.persist`).
            approved_by: Who allowed it, POLICY or HUMAN; required with `reason` iff `persist`.
            reason: Why, one line; required with `approved_by` iff `persist`.

        Raises:
            pydantic.ValidationError: `persist`/`approved_by`/`reason` disagree (`RestoreRecord`).
        """
        resolved, within_scratch = _resolve_touched(path, self.scratch_root)
        if not within_scratch:
            self._restore_records.append(
                _build_restore_record(resolved, prior, persist, approved_by, reason)
            )

    def note_allowed_path(self, path: Path) -> None:
        """Widen `allowed_paths` to also cover `path` (resolved), if not already (roadmap 5.0e)."""
        resolved = path.resolve(strict=False)
        if self.is_path_allowed(resolved) or len(self.allowed_paths) >= MAX_ALLOWED_PATHS:
            return  # Already reachable, or this lease has already widened as far as it may.
        self.allowed_paths = (*self.allowed_paths, resolved)

    def is_path_allowed(self, path: Path) -> bool:
        """Return whether `path`, resolved (`..`/symlinks collapsed), is reachable from this lease.

        True when it lands inside `scratch_root` or under one of `allowed_paths`.
        """
        resolved = path.resolve(strict=False)
        roots = (self.scratch_root, *self.allowed_paths)
        return any(_is_within(resolved, root.resolve(strict=False)) for root in roots)

    async def release(self) -> LeaseReleaseReport:
        """Release this lease, idempotently.

        Transitions RELEASING -> RELEASED, delegating to the injected LeaseReleaser, then records
        cell.released. A delegate that raises moves the lease to ORPHANED and re-raises instead
        (`_orphan_after_failed_release`), so a later call is legal (`ORPHANED -> RELEASING`)
        rather than stuck forever; a later call after success just returns the same report.

        Returns:
            The outcome of releasing this lease.

        Raises:
            InvalidLeaseTransitionError: This lease is not OPEN or ORPHANED when called.
        """
        if self._release_report is not None:
            return self._release_report  # Idempotent: nothing to redo.
        assert_transition(self.state, LeaseState.RELEASING, lease_id=self.id)
        self.state = LeaseState.RELEASING
        try:
            report = await self._releaser.release(self)
        except Exception:
            self._orphan_after_failed_release()
            raise
        assert_transition(self.state, LeaseState.RELEASED, lease_id=self.id)
        self.state = LeaseState.RELEASED
        self._release_report = report
        await self._record(
            "cell.released",
            {
                "lease_id": self.id,
                "holder": self.holder,
                "task_id": self.task_id,
                "access_level": self.access_level.value,
                "comb_shield": self.comb_shield.value,
                "killed_processes": report.killed_processes,
                "residual_paths": len(report.residual_paths),
                "is_restored": report.is_restored,
            },
        )
        return report

    def _orphan_after_failed_release(self) -> None:
        """Move a RELEASING lease to ORPHANED after its delegate raised, so a retry is legal.

        SAFETY: `release()`'s broad `except Exception` exists only to run this compensating
        transition before re-raising the same exception unchanged; it is never swallowed here.
        """
        assert_transition(self.state, LeaseState.ORPHANED, lease_id=self.id)
        self.state = LeaseState.ORPHANED

    async def _record(self, kind: str, payload: Mapping[str, JsonValue]) -> None:
        """Build and record a CellEvent for this lease's Cell, stamped with its own identity."""
        event = CellEvent(
            id=new_event_id(self._clock),
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            at=self._clock.now(),
            actor=self._identity.actor,
            kind=kind,
            subject_id=self.cell_id,
            payload=dict(payload),
        )
        await self._trail.record(event)


def _build_restore_record(
    path: Path,
    prior: bytes | None,
    persist: bool,
    approved_by: ApprovedBy | None,
    reason: str | None,
) -> RestoreRecord:
    """Build the RestoreRecord `note_restore_path` appends; split out to keep that method short."""
    return RestoreRecord(
        path=path, prior=prior, persist=persist, approved_by=approved_by, reason=reason
    )


def _resolve_touched(path: Path, scratch_root: Path) -> tuple[Path, bool]:
    """Resolve `path` and report whether it lands inside `scratch_root`.

    Module-level (not a `RealCellLease` method) purely to keep that class within codingrules
    5.1's class-length limit; `note_touched_path` and `note_restore_path` are its only callers.
    """
    resolved = path.resolve(strict=False)
    return resolved, _is_within(resolved, scratch_root.resolve(strict=False))


def _is_within(path: Path, root: Path) -> bool:
    """Return whether `path` equals `root` or is somewhere underneath it.

    Args:
        path: An already-resolved candidate path.
        root: An already-resolved directory to check containment against.

    Returns:
        True if `path == root` or `root` is one of `path`'s parents.
    """
    return path == root or root in path.parents
