"""Define the Cell isolation levers' bodies: what the human asks for, and what came of it.

The human's two levers on a Cell (roadmap step 10.6a, ADR-0035) take one small body and answer
with what the Queen's one isolation path, or her lift, actually did. `IsolateBody` names why, in
the human's own short words for the trail, and optionally the Guard report the order answers (the
Hive Stand fallback's CRITICAL Alarm names one; citing it dates the taint from its first event).
`CellIsolationView` and `CellLiftView` are the Landing Board's own stable shape for the Queen's
outcomes: ids, enum values and counts, never content.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.isolation``.
    Used by its ``cells`` routes; published in the OpenAPI document. Calls into
    ``hivemind.queen.isolation`` (the outcomes it shapes) and pydantic only.

Key invariants:
    - Every body is frozen and forbids extras; every string is bounded.

See Also:
    - hivemind.queen.isolation.order for IsolationOutcome and LiftOutcome.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hivemind.queen.isolation import MAX_ISOLATION_REASON_CHARS, IsolationOutcome, LiftOutcome

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.
_REPORT_ID = r"^guardrep_[0-9A-HJKMNP-TV-Z]{26}$"  # hivemind.guard.GUARD_REPORT_ID_PATTERN.

__all__ = ["CellIsolationView", "CellLiftView", "IsolateBody", "isolation_view", "lift_view"]


class IsolateBody(BaseModel):
    """Isolate one Cell: why, and the Guard report the order answers, if any."""

    model_config = _CONFIG

    reason: str = Field(
        min_length=1,
        max_length=MAX_ISOLATION_REASON_CHARS,
        description="Why, in a short phrase for the trail (ids, not content).",
    )
    report_id: str | None = Field(
        default=None,
        pattern=_REPORT_ID,
        description="A Guard report the Queen was sent; its first cited event dates the taint.",
    )


class CellIsolationView(BaseModel):
    """What isolating a Cell did: isolated it, or found it isolated already."""

    model_config = _CONFIG

    cell_id: str = Field(description="The Cell.")
    isolated: bool = Field(description="This order isolated it (cell.isolated recorded).")
    already_isolated: bool = Field(description="It was isolated before; nothing changed.")
    event_id: str | None = Field(description="The cell.isolated event, when isolated now.")
    wax_id: str | None = Field(description="The BLOCK Cell Wax note that keeps placement off it.")
    revoked_grant_ids: list[str] = Field(description="The Warden's grants revoked.")
    paused_task_ids: list[str] = Field(description="The tasks paused on it.")
    unacknowledged_task_ids: list[str] = Field(
        description="Paused tasks whose bee did not answer within the bound."
    )
    egress: str = Field(description="cut, unsupported, untracked (a Real Cell) or failed.")
    tainted_count: int = Field(ge=0, description="Memory items newly tainted.")


class CellLiftView(BaseModel):
    """What lifting a Cell did; tainted memory stays tainted until a judge clears it."""

    model_config = _CONFIG

    cell_id: str = Field(description="The Cell.")
    event_id: str = Field(description="The cell.isolation_lifted event.")
    isolated_event_id: str | None = Field(
        description="The cell.isolated event it ended; null when only placement holds stood."
    )
    wax_cleared: str | None = Field(description="The BLOCK Cell Wax note it cleared.")
    egress: str = Field(description="restored, unsupported, untracked or failed.")
    released_holds: int = Field(ge=0, description="The Queen's placement holds released.")


def isolation_view(outcome: IsolationOutcome) -> CellIsolationView:
    """Shape the Queen's isolation outcome for the Landing Board.

    Args:
        outcome: What the one isolation path did.

    Returns:
        The view.
    """
    return CellIsolationView(
        cell_id=outcome.cell_id,
        isolated=outcome.isolated,
        already_isolated=outcome.already_isolated,
        event_id=outcome.event_id,
        wax_id=outcome.wax_id,
        revoked_grant_ids=list(outcome.revoked_grant_ids),
        paused_task_ids=list(outcome.paused_task_ids),
        unacknowledged_task_ids=list(outcome.unacknowledged_task_ids),
        egress=outcome.egress.value,
        tainted_count=outcome.tainted_count,
    )


def lift_view(outcome: LiftOutcome) -> CellLiftView:
    """Shape the Queen's lift outcome for the Landing Board.

    Args:
        outcome: What the human's lift did.

    Returns:
        The view.
    """
    return CellLiftView(
        cell_id=outcome.cell_id,
        event_id=outcome.event_id,
        isolated_event_id=outcome.isolated_event_id,
        wax_cleared=outcome.wax_cleared,
        egress=outcome.egress.value,
        released_holds=outcome.released_holds,
    )
