"""Define Leaving and ApprovedBy: one file a Leavings ledger row remembers past a lease's release.

A **Leaving** is a file a task's `RestoreRecord` (`hivemind.cell.lease`) was allowed to keep,
instead of `release()` restoring it to what it held before (codingrules section 8.7: a Real Cell is
still left as found, *plus exactly the paths the Leavings ledger lists* -- roadmap phase 5
preamble). `ApprovedBy` names who allowed that: `POLICY` (an autopilot rule in a later step,
5.0c/5.0d) or `HUMAN` (an operator, asked because the policy said so). `Leaving` carries `prior`,
the bytes (or `None`, meaning "did not exist before") the path held before the write that produced
it -- not one of the roadmap step's own listed fields, but `hive cells leavings remove` needs
exactly those bytes to replay (its own roadmap sentence: "remove replays the stored prior bytes (or
unlinks)"), and a `Leaving`'s own row is the only place that survives past the lease that wrote it,
the same reason `hivemind.cell.lease.RestoreRecord.prior` exists in the first place. Display code
(`hive cells leavings list`) never prints it (codingrules section 12: never log full page
contents); it is read back only by `remove`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell.leavings`. Built
    by `hivemind.cell.local.releaser.HiveStandLeaseReleaser.release` from a persisted
    `RestoreRecord` and a lease's own facts, and by `hive cells leavings remove`'s own read path.
    Stored and returned by `hivemind.cell.leavings.store_protocol.LeavingsStore` implementations.
    Calls into waggle only.

Key invariants:
    - Frozen and forbids extra fields, like every boundary value in this repository.
    - `removed_at` is `None` until `hive cells leavings remove` marks the row; once set, it is
      never cleared back to `None` (a fresh leaving at the same path is a new row, not a reopened
      one -- `LeavingsStore.record_leaving`'s own docstring).
    - `sha256`/`size` describe the content actually left in place (the *new* bytes), never
      `prior` (the *old* bytes `prior` would restore); the two are computed at different times by
      the releaser (`prior` at write time, `sha256`/`size` by reading the file at release time).

See Also:
    - .claude/roadmap.md step 5.0a for "Leaving (Cell id, path, sha256, size, task, lease,
      approved_by = POLICY | HUMAN, the reason, left and removed times)".
    - hivemind.cell.lease for RestoreRecord, the per-lease bookkeeping a Leaving is built from.
    - hivemind.cell.leavings.store_protocol for LeavingsStore, the protocol this model is read
      and written through.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from waggle.messages.base import (
    MAX_REASON_CHARS,
    CellIdField,
    LeaseIdField,
    TaskIdField,
    UtcDatetime,
)

# A sha256 hex digest is always 64 lowercase hex characters (hashlib.sha256(...).hexdigest()).
SHA256_HEX_CHARS = 64

__all__ = ["SHA256_HEX_CHARS", "ApprovedBy", "Leaving"]


class ApprovedBy(StrEnum):
    """Who allowed a path to stay, instead of being restored on release.

    A bee never decides this alone (roadmap phase 5 preamble): `POLICY` names an autopilot rule
    (`hivemind.supervision.capping.leave`, roadmap step 5.0c) that decided without asking;
    `HUMAN` names an operator the policy asked (roadmap step 5.0d's `keep` tool).
    """

    POLICY = "policy"
    HUMAN = "human"


class Leaving(BaseModel):
    """One Leavings ledger row: a path a Cell keeps past its lease's release, and who allowed it.

    Written by `hivemind.cell.local.releaser.HiveStandLeaseReleaser.release` for every
    `RestoreRecord` a Capping proposal (`hivemind.supervision.capping.apply`) recorded with
    `persist=True`; read and marked by `hive cells leavings list|remove`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_id: CellIdField = Field(description="The Cell this path was left on.")
    path: Path = Field(description="The resolved, absolute path outside scratch that was left.")
    sha256: str = Field(
        min_length=SHA256_HEX_CHARS,
        max_length=SHA256_HEX_CHARS,
        description="hashlib.sha256(...).hexdigest() of the content left at `path`.",
    )
    size: int = Field(ge=0, description="The size, in bytes, of the content left at `path`.")
    task_id: TaskIdField | None = Field(
        default=None, description="The task whose lease wrote this path; None for internal use."
    )
    lease_id: LeaseIdField = Field(description="The lease that released with this path left.")
    approved_by: ApprovedBy = Field(description="Who allowed this path to stay: POLICY or HUMAN.")
    reason: str = Field(
        max_length=MAX_REASON_CHARS, description="Why this path was allowed to stay, one line."
    )
    prior: bytes | None = Field(
        description="The bytes `path` held before the write that produced this Leaving, or None "
        "when it did not exist yet; `remove` replays this, never displayed by `list`."
    )
    left_at: UtcDatetime = Field(description="When release() recorded this row.")
    removed_at: UtcDatetime | None = Field(
        default=None, description="When `hive cells leavings remove` replayed and marked this row."
    )
