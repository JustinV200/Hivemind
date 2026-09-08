"""Define the cell family's tenancy lifecycle: a Cell asked for, a lease opened, ended, released.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). A Cell is
a unit of compute: a Virtual Cell the Hive provisions, or a Real Cell, an existing device borrowed
for a task and left exactly as found. A lease is one Warden's (the always-on supervisor of one
Cell's) tenancy on a Cell, with a scratch directory and the processes it started, and the four
messages here are its life: ``CellRequest`` asks for a Cell (a Warden states task needs and the
Queen, the central orchestrator, places; or the Queen names a Real Cell and asks its gateway or
Warden to open a lease for a holder), ``LeaseOpened`` answers it, ``CellTeardownRequest`` asks
to release a lease or retire a Virtual Cell, gracefully or immediately, and ``LeaseReleased``
answers that, saying whether the device was left as found. The Cell's own half of the family
(cell.ready, cell.heartbeat) and the enums and value models these messages share live in
``waggle.messages.cell``, and the Cell Wax notes in ``waggle.messages.cell_wax``, split out by
responsibility so every file stays under the codingrules 5.1 size limit. Every bound is a named
constant here; the number, not the name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built and read by the Queen, every Warden and every Pollen Packet
    (the thin gateway on an enrolled device, which opens and releases leases on it); calls into
    waggle.messages.base, waggle.messages.labels and waggle.messages.cell only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.
    - A CellRequest is exactly one of its two forms: needs without a Cell or holder, or a Cell
      with a holder; and a LeaseReleased that claims restoration lists no residual path.

See Also:
    - docs/waggle/spec.md section 8.5 for the normative fields, bounds and validators.
    - waggle.messages.cell for CellReady, CellHeartbeat, ReleaseCause and TaskNeedsReport.
    - waggle.messages.registry for the kinds these classes are registered under.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from waggle.messages.base import (
    MAX_PATH_CHARS,
    MAX_REASON_CHARS,
    CellIdField,
    LeaseIdField,
    TaskIdField,
    WaggleMessage,
    WardenIdField,
)
from waggle.messages.cell import ReleaseCause, TaskNeedsReport
from waggle.messages.labels import AccessLevel, CombShieldLevel, Urgency

MIN_SCRATCH_ROOT_CHARS = 1  # A lease always has a scratch directory; an empty path names none.
MAX_ALLOWED_PATHS = 64  # Paths outside scratch a lease may touch; more means scratch is misplaced.
MAX_RESIDUAL_PATHS = 64  # Paths that could not be restored, at most the allowed paths plus scratch.

__all__ = [
    "MAX_ALLOWED_PATHS",
    "MAX_RESIDUAL_PATHS",
    "MIN_SCRATCH_ROOT_CHARS",
    "CellRequest",
    "CellTeardownRequest",
    "LeaseOpened",
    "LeaseReleased",
]


# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]
# A path on the leased Cell, bounded like every path on the wire.
_Path = Annotated[str, Field(max_length=MAX_PATH_CHARS)]
# The causes a teardown may be asked for: the ones a bee or a human decides, never the ones a
# gateway or a sweep discovers on its own (DEAD_MAN, ORPHAN_SWEEP, HOLDER_LOST).
_REQUESTABLE_CAUSES = frozenset(
    {ReleaseCause.COMPLETED, ReleaseCause.CANCELLED, ReleaseCause.STING_CUT}
)


class CellTeardownRequest(WaggleMessage):
    """Ask the recipient to end a tenancy (cell.teardown_request, a request).

    Release a lease on a Real Cell, or retire a Virtual Cell, gracefully or immediately. A
    Warden asks the Queen; the Queen asks the device's gateway or Warden. The answer is a
    correlated LeaseReleased or a control.error.
    """

    cell_id: CellIdField = Field(description="The Cell concerned.")
    lease_id: LeaseIdField | None = Field(
        description="The lease to release; None asks for the whole Virtual Cell to be retired "
        "(overwinter or destroy is the Queen's choice)."
    )
    urgency: Urgency = Field(description="Graceful or immediate.")
    cause: ReleaseCause = Field(
        description="Why the tenancy ends, so a gateway behaves differently for a Sting Cut "
        "without deciding anything itself; only COMPLETED, CANCELLED or STING_CUT on a "
        "request, and the answering LeaseReleased.cause echoes it."
    )
    reason: _Reason = Field(description="Why.")

    @model_validator(mode="after")
    def _cause_is_requestable(self) -> CellTeardownRequest:
        """Reject a cause only a gateway or a sweep can establish."""
        # DEAD_MAN, ORPHAN_SWEEP and HOLDER_LOST describe what the releaser found, not what a
        # requester decided; a request carrying one would be a bee claiming a fact it cannot
        # know, so only the decided causes are accepted.
        if self.cause not in _REQUESTABLE_CAUSES:
            raise ValueError(
                f"A CellTeardownRequest may carry only COMPLETED, CANCELLED or STING_CUT, got "
                f"{self.cause.value}."
            )
        return self


class CellRequest(WaggleMessage):
    """Ask for a Cell, or for a lease on a named Cell (cell.request, a request).

    A Warden states task needs and the Queen places; or the Queen names a Real Cell and asks its
    gateway or Warden to open a lease for a holder at an access level. The answer is a
    correlated LeaseOpened or a control.error.
    """

    cell_id: CellIdField | None = Field(
        description="None means any Cell that fits needs; set means open a lease on exactly "
        "this Cell."
    )
    needs: TaskNeedsReport | None = Field(
        description="What the work needs; required when cell_id is None."
    )
    holder: WardenIdField | None = Field(
        description="The Warden that will hold the resulting lease; required when cell_id is "
        "set, None when it is None: every placed Cell gets its own Warden, which the Queen "
        "names in the correlated LeaseOpened."
    )
    task_id: TaskIdField | None = Field(
        description="The task the Cell is for; None for a sandbox or other internal use."
    )
    access_level: AccessLevel = Field(
        description="The most the holder needs; the operator's enrolled level caps it."
    )
    lifetime_s: Annotated[float, Field(gt=0)] | None = Field(
        description="Expected tenancy length, in seconds; None means until released."
    )
    reason: _Reason = Field(description="Why the Cell is needed.")

    @model_validator(mode="after")
    def _fields_match_form(self) -> CellRequest:
        """Require needs for a placement ask, and a holder exactly for a named-Cell ask."""
        # The two forms are told apart by cell_id alone: placement needs something to place on,
        # and a named Cell needs the Warden its lease is for, while a placement ask names no
        # holder because the Queen appoints one. Both directions are checked.
        placing = self.cell_id is None
        if placing and self.needs is None:
            raise ValueError("A CellRequest with no cell_id must state needs to place on.")
        if placing != (self.holder is None):
            raise ValueError(
                f"CellRequest holder is required exactly when cell_id is set, got cell_id "
                f"{self.cell_id} with holder {self.holder}."
            )
        return self


class LeaseOpened(WaggleMessage):
    """Announce a lease as open (cell.lease_opened, an event).

    The holder, task, access and tier, the scratch root, the paths allowed outside scratch and
    the dead-man limit, with the placement reason. The opener tells the Queen; the Queen tells
    the holder.
    """

    lease_id: LeaseIdField = Field(description="Minted by the opener.")
    cell_id: CellIdField = Field(description="The Cell leased.")
    holder: WardenIdField = Field(description="The Warden that owns the tenancy.")
    task_id: TaskIdField | None = Field(description="The task it serves, if any.")
    access_level: AccessLevel = Field(description="The level actually granted.")
    comb_shield: CombShieldLevel = Field(description="The Cell's tier the task inherits.")
    scratch_root: str = Field(
        min_length=MIN_SCRATCH_ROOT_CHARS,
        max_length=MAX_PATH_CHARS,
        description="The lease's scratch directory; everything outside it needs a capability.",
    )
    allowed_paths: tuple[_Path, ...] = Field(
        max_length=MAX_ALLOWED_PATHS,
        description="Paths outside scratch the lease may touch.",
    )
    dead_man_s: Annotated[float, Field(gt=0)] | None = Field(
        description="If the link is lost longer than this and no Warden runs on the device, "
        "the gateway kills what the lease started and releases; None when a Warden lives on "
        "the Cell."
    )
    reason: _Reason = Field(description="Why this Cell, any Cell Wax that weighed on it included.")


class LeaseReleased(WaggleMessage):
    """Announce a lease as released (cell.lease_released, an event).

    Why, whether the device was left as found, how many started processes were killed, and any
    paths that could not be restored. A gateway may send it unprompted (dead-man).
    """

    lease_id: LeaseIdField = Field(description="The lease closed.")
    cell_id: CellIdField = Field(description="The Cell it was on.")
    holder: WardenIdField = Field(description="The Warden that held it.")
    cause: ReleaseCause = Field(description="Which rule released it.")
    is_restored: bool = Field(
        description="True when the scratch directory is gone and every touched path was restored."
    )
    killed_processes: int = Field(
        ge=0, description="Processes the lease started that were terminated on release."
    )
    residual_paths: tuple[_Path, ...] = Field(
        max_length=MAX_RESIDUAL_PATHS,
        description="Paths outside scratch that could not be restored; empty exactly when "
        "is_restored.",
    )
    reason: _Reason = Field(description="The releaser's reason.")

    @model_validator(mode="after")
    def _residuals_match_restoration(self) -> LeaseReleased:
        """Require residual paths exactly when the device was not left as found."""
        # "Left as found" with a residual path is a contradiction, and "not restored" with no
        # path named leaves the Undertaker nothing to clean; both directions are checked.
        if self.is_restored == bool(self.residual_paths):
            raise ValueError(
                f"LeaseReleased residual_paths must be empty exactly when is_restored, got "
                f"is_restored {self.is_restored} with {len(self.residual_paths)} path(s)."
            )
        return self
