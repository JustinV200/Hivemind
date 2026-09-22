"""Define the cell family's snapshot relay: a Virtual Cell's Warden asks the Queen to snapshot it.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A Virtual
Cell (a VM or container the Hive provisions) runs its Warden *inside* the Cell (ADR-0027), so that
Warden cannot reach the host's Docker daemon or QEMU process to snapshot itself before a risky
Capping proposal (ADR-0018) -- only the Queen, on the Hive Stand, holds that backend. The four
messages here are the relay: ``CellSnapshotRequest`` asks the Queen to snapshot the sender's own
Cell (a Warden's `wardens.snapshot_relay.RelaySnapshotter` sends it over its `queen_link`) and
``CellSnapshotReply`` answers with the new snapshot's id or why it could not be taken; likewise
``CellRollbackRequest``/``CellRollbackReply`` for rolling a Cell back to a snapshot it already took.
Both replies carry their own outcome (a snapshot id or an error; ``ok`` or an error) rather than
falling back to ``control.error``, so a Warden's relay never has to special-case the two kinds of
answer a request can draw (PROTOCOL_MINOR 5, roadmap step 5.10's follow-up gap). Every bound is a
named constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; a request is built by a Virtual Cell's own Warden and read by the
    Queen, a reply is built by the Queen and read by that Warden; calls into waggle.messages.base
    only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - A CellSnapshotReply carries exactly one of `snapshot_id`/`error`; a CellRollbackReply carries
      `error` exactly when `ok` is False (validators).

See Also:
    - docs/waggle/spec.md section 8.5 for the normative fields, bounds and validators.
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for the Snapshotter contract a
      timed-out or errored reply falls back from (SnapshotUnsupportedError -> REVERSE_DIFF).
    - waggle.messages.cell.status and waggle.messages.cell.leases for the rest of the family.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import MAX_REASON_CHARS, CellIdField, WaggleMessage

MIN_PURPOSE_CHARS = 1  # A purpose always says something: which Capping tier asked for this.
MAX_PURPOSE_CHARS = MAX_REASON_CHARS  # Same bound as a reason: a short sentence, never an essay.
MIN_SNAPSHOT_ID_CHARS = 1  # A snapshot id is always non-empty once minted.
MAX_SNAPSHOT_ID_CHARS = 256  # hivemind.cell.snapshot.SnapshotId strings; generous for any backend.
SNAPSHOT_ID_PATTERN = r"^[A-Za-z0-9_.:-]+$"  # No IdKind exists for it; a plain token, never a path.
MIN_ERROR_CHARS = 1  # An error always says why.
MAX_ERROR_CHARS = MAX_REASON_CHARS  # Same bound as a reason.

__all__ = [
    "MAX_ERROR_CHARS",
    "MAX_PURPOSE_CHARS",
    "MAX_SNAPSHOT_ID_CHARS",
    "MIN_ERROR_CHARS",
    "MIN_PURPOSE_CHARS",
    "MIN_SNAPSHOT_ID_CHARS",
    "SNAPSHOT_ID_PATTERN",
    "CellRollbackReply",
    "CellRollbackRequest",
    "CellSnapshotReply",
    "CellSnapshotRequest",
]

# A snapshot id as hivemind.cell.snapshot.SnapshotId serialises it: a plain bounded token, since
# waggle may never import hivemind (layer rule) and no IdKind exists for one.
_SnapshotId = Annotated[
    str, Field(min_length=MIN_SNAPSHOT_ID_CHARS, max_length=MAX_SNAPSHOT_ID_CHARS)
]
# Why the sender wants this snapshot now (the Capping tier or proposal kind about to run), as the
# catalogue conventions would name a `reason` field, but distinct because this one never explains a
# decision already made -- it is read before the snapshot is taken, not recorded after one.
_Purpose = Annotated[str, Field(min_length=MIN_PURPOSE_CHARS, max_length=MAX_PURPOSE_CHARS)]
# A full-sentence failure explanation, the same shape control.error.message takes.
_Error = Annotated[str, Field(min_length=MIN_ERROR_CHARS, max_length=MAX_ERROR_CHARS)]


class CellSnapshotRequest(WaggleMessage):
    """Ask the Queen to snapshot the sender's own Cell (cell.snapshot_request, a request).

    Sent by a Virtual Cell's own Warden, which cannot reach the host backend itself (ADR-0027).
    The answer is a correlated CellSnapshotReply.
    """

    cell_id: CellIdField = Field(description="The Cell to snapshot; always the sender's own Cell.")
    purpose: _Purpose = Field(
        description="Why: the Capping tier or proposal kind this snapshot precedes."
    )


class CellSnapshotReply(WaggleMessage):
    """Answer a CellSnapshotRequest with a new snapshot id or why one could not be taken.

    (cell.snapshot_reply, a reply.) An unknown Cell, or a Cell whose backend cannot snapshot at
    all, answers with `error` set; the requesting Warden's own relay treats any error the same
    way -- SnapshotUnsupportedError, so the Capping gate falls back to REVERSE_DIFF (ADR-0018).
    """

    cell_id: CellIdField = Field(description="The Cell the request named.")
    snapshot_id: _SnapshotId | None = Field(
        description="The new snapshot's id; set exactly when error is None."
    )
    error: _Error | None = Field(
        description="Why no snapshot was taken; set exactly when snapshot_id is None."
    )

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> CellSnapshotReply:
        """Require exactly one of snapshot_id and error."""
        if (self.snapshot_id is None) == (self.error is None):
            raise ValueError(
                f"CellSnapshotReply sets exactly one of snapshot_id and error, got snapshot_id "
                f"{self.snapshot_id!r} and error {self.error!r}."
            )
        return self


class CellRollbackRequest(WaggleMessage):
    """Ask the Queen to roll the sender's own Cell back to a snapshot (cell.rollback_request).

    A request; sent by the same Warden a CellSnapshotRequest would be. The answer is a correlated
    CellRollbackReply.
    """

    cell_id: CellIdField = Field(description="The Cell to roll back; always the sender's own Cell.")
    snapshot_id: _SnapshotId = Field(
        description="An id a prior CellSnapshotReply on this Cell returned."
    )


class CellRollbackReply(WaggleMessage):
    """Answer a CellRollbackRequest with whether the rollback succeeded (cell.rollback_reply).

    A reply. `error` is set exactly when `ok` is False: an unknown Cell, an unknown snapshot id,
    or a backend failure while restoring it.
    """

    cell_id: CellIdField = Field(description="The Cell the request named.")
    ok: bool = Field(description="Whether the Cell was restored to the named snapshot.")
    error: _Error | None = Field(description="Why the rollback failed; set exactly when not ok.")

    @model_validator(mode="after")
    def _error_matches_ok(self) -> CellRollbackReply:
        """Require error set exactly when ok is False."""
        if self.ok == (self.error is not None):
            raise ValueError(
                f"CellRollbackReply error must be set exactly when ok is False, got ok {self.ok} "
                f"with error {self.error!r}."
            )
        return self
