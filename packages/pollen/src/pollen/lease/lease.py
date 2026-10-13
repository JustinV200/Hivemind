"""Define DeviceLease, the device-side half of a Real Cell lease, and its state machine.

A Real Cell is an existing device the Hive borrows for a task and leaves exactly as found, and a
lease (the Hive's temporary hold on it) is how: a scratch directory of its own, the processes it
started, and a record of every path it changed outside scratch with what that path held before.
The Warden (the per-Cell supervisor) that holds the lease lives on the Hive Stand (the machine
the Queen, the Hive's central orchestrator, runs on); this is the half the Pollen Packet (the
thin gateway program on the device) keeps, in its lease table, so that a crash leaks neither
scratch nor a process. ``LeaseState`` is the machine of codingrules Appendix C: ``OPEN`` until a
release begins, ``RELEASING`` while it runs (and again if a step failed or the packet restarted
mid-release: every step is idempotent and runs again), ``RELEASED`` when the lease leaves the
table. The edges live in ``TRANSITIONS`` and nowhere else.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Persisted by
    pollen.lease.table; changed by the executors (a started process, a restore record) and by
    pollen.agent.leases (opening and releasing); calls into waggle value types and
    pollen.errors only.

Key invariants:
    - A lease never leaves RELEASED, and nothing but RELEASING precedes it.
    - A DeviceLease is a frozen value: every change returns a new one, and only the table that
      owns the lease replaces the stored copy.
    - Paths in a record are resolved absolute paths; a path appears in at most one restore
      record, the first change to it, because the earliest prior state is the one to put back.

See Also:
    - .claude/codingrules.md Appendix C (Device lease) for the machine and where it persists.
    - docs/waggle/gateway-contract.md sections 9.1 and 12 for opening, releasing and restoring.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from pollen.errors import InvalidTransitionError
from waggle.messages.base import (
    MAX_PATH_CHARS,
    CellIdField,
    LeaseIdField,
    TaskIdField,
    UtcDatetime,
    WardenIdField,
)
from waggle.messages.cell import ReleaseCause
from waggle.messages.labels import AccessLevel

__all__ = [
    "TRANSITIONS",
    "DeviceLease",
    "LeaseState",
    "ProcessKind",
    "ProcessRecord",
    "RestoreKind",
    "RestoreRecord",
    "transition",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class LeaseState(Enum):
    """Where a device lease is in its life (codingrules Appendix C)."""

    OPEN = "OPEN"  # Session traffic is accepted.
    RELEASING = "RELEASING"  # A release is under way, or failed a step and will run again.
    RELEASED = "RELEASED"  # Everything undone; the lease has left the table.


# The single transition table. Every allowed edge carries the reason it exists.
TRANSITIONS: Mapping[LeaseState, frozenset[LeaseState]] = MappingProxyType(
    {
        # A teardown request, the dead-man switch or an orphan sweep begins the release.
        LeaseState.OPEN: frozenset({LeaseState.RELEASING}),
        # Finished (every step succeeded), or a step failed and the same release runs again.
        LeaseState.RELEASING: frozenset({LeaseState.RELEASING, LeaseState.RELEASED}),
        # Final: a released lease is gone, and a new lease gets a new id.
        LeaseState.RELEASED: frozenset(),
    }
)


def transition(current: LeaseState, new: LeaseState) -> LeaseState:
    """Return ``new`` when ``current -> new`` is an allowed edge.

    Args:
        current: The state the lease is in.
        new: The state it should take.

    Returns:
        ``new``, now known to be a legal successor of ``current``.

    Raises:
        InvalidTransitionError: The edge is not in TRANSITIONS.
    """
    if new not in TRANSITIONS[current]:
        raise InvalidTransitionError(
            f"A device lease cannot go from {current.value} to {new.value}."
        )
    return new


class ProcessKind(Enum):
    """How a recorded process was started."""

    EXEC = "EXEC"  # A session.exec: it ends on its own and its record is pruned once it is gone.
    BACKGROUND = "BACKGROUND"  # A session.process START: it lives until stopped or released.


class RestoreKind(Enum):
    """What a release must do to put one recorded path back."""

    CREATED_FILE = "CREATED_FILE"  # The lease created the file: delete it.
    CREATED_DIR = "CREATED_DIR"  # The lease created the directory: remove it if it is empty.
    REPLACED = "REPLACED"  # The path held bytes before: write them back, with their mode.


class ProcessRecord(BaseModel):
    """One process the lease started: enough to find its group again after a restart."""

    model_config = _MODEL_CONFIG

    pid: int = Field(ge=1, description="The process id, also its process-group id.")
    created: float = Field(
        ge=0, description="The process's creation time, so a recycled pid is never signalled."
    )
    kind: ProcessKind = Field(description="An exec, or a background process.")


class RestoreRecord(BaseModel):
    """One path the lease changed outside scratch, and what a release must do about it."""

    model_config = _MODEL_CONFIG

    path: str = Field(max_length=MAX_PATH_CHARS, description="The resolved absolute path.")
    kind: RestoreKind = Field(description="Delete it, remove it if empty, or write bytes back.")
    backup: str | None = Field(
        default=None, description="The file inside the lease's backup directory holding the bytes."
    )
    mode: int | None = Field(
        default=None, ge=0, description="The permission bits the path had; REPLACED only."
    )


class DeviceLease(BaseModel):
    """The device-side record of one lease, persisted in the lease table."""

    model_config = _MODEL_CONFIG

    lease_id: LeaseIdField = Field(description="Minted by the packet when the lease opened.")
    cell_id: CellIdField = Field(description="The Real Cell this device is.")
    holder: WardenIdField = Field(description="The Warden that owns the tenancy.")
    task_id: TaskIdField | None = Field(default=None, description="The task it serves, if any.")
    access_level: AccessLevel = Field(description="The level granted when it opened.")
    state: LeaseState = Field(
        default=LeaseState.OPEN, description="Where the lease is in its life."
    )
    scratch_root: str = Field(description="The lease's scratch directory, resolved and absolute.")
    quota_bytes: Annotated[int, Field(ge=0)] = Field(
        description="The most bytes the scratch directory may hold."
    )
    opened_at: UtcDatetime = Field(description="When the lease opened.")
    processes: tuple[ProcessRecord, ...] = Field(
        default=(), description="Every process the lease started and still tracks."
    )
    restores: tuple[RestoreRecord, ...] = Field(
        default=(), description="Every path changed outside scratch, in the order first changed."
    )
    is_session_closed: bool = Field(
        default=False, description="A session.close ended session traffic; the lease stays open."
    )
    release_cause: ReleaseCause | None = Field(
        default=None, description="Why the release began; set from RELEASING on."
    )

    def with_process(self, record: ProcessRecord) -> DeviceLease:
        """Return a copy that also tracks ``record``."""
        kept = tuple(p for p in self.processes if p.pid != record.pid)
        return self.model_copy(update={"processes": (*kept, record)})

    def without_process(self, pid: int) -> DeviceLease:
        """Return a copy that no longer tracks ``pid``."""
        kept = tuple(p for p in self.processes if p.pid != pid)
        return self.model_copy(update={"processes": kept})

    def with_restore(self, records: tuple[RestoreRecord, ...]) -> DeviceLease:
        """Return a copy that also records ``records``, skipping paths already recorded."""
        known = {r.path for r in self.restores}
        fresh = tuple(r for r in records if r.path not in known)
        return self.model_copy(update={"restores": (*self.restores, *fresh)})

    def releasing(self, cause: ReleaseCause) -> DeviceLease:
        """Return a copy in RELEASING (an allowed edge from OPEN and from RELEASING)."""
        state = transition(self.state, LeaseState.RELEASING)
        return self.model_copy(update={"state": state, "release_cause": cause})

    def is_recorded(self, path: str) -> bool:
        """Return whether a restore record already exists for ``path``."""
        return any(r.path == path for r in self.restores)
