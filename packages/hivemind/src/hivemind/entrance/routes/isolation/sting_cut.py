"""Serve the human's Sting Cut lever: cut one Cell off, by its id or a lease on it (roadmap 13.4a).

``POST /v1/cells/{target}/sting-cut`` is the human's path for any Cell, the Hive Stand's own lease
included. A Sting Cut is break-glass (codingrules 15, ADR-0041): it needs an interactive device
inside its step-up window (``require_step_up`` with nothing to hold: a program cannot step up, so
it is refused, never queued) and the typed phrase in the body (``check_break_glass``), on top of
``entrance:steward``, the human's own family. ``target`` is a ``cell_...`` id or a ``lease_...`` id,
which the Queen resolves to the Cell its lease was opened on. The cut goes through the Queen's door
(``QueenDoor.sting_cut``, the one cut path); the Entrance never writes a Queen table itself.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.isolation``.
    Registered in the route table. Calls into the step-up gate, the break-glass check, the Queen's
    door and ``hivemind.queen.sting_cut``'s outcome only.

Key invariants:
    - No cut happens without an interactive, stepped-up session presenting the phrase.
    - Every refusal keeps its category: 404 for an unknown Cell, lease or report, 403 for a cut
      the point refused or a missing phrase.

See Also:
    - hivemind.queen.sting_cut for what the lever does.
    - docs/guard/sting-cut.md for the operator's view.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field

from hivemind.entrance.auth.step_up import ActionKind, check_break_glass
from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.gate.step_up import require_step_up
from hivemind.entrance.routes.isolation.cells import STEWARD
from hivemind.queen.sting_cut import MAX_STING_CUT_REASON_CHARS, StingCutOutcome

_CONFIG = ConfigDict(frozen=True, extra="forbid")
_REPORT_ID = r"^guardrep_[0-9A-HJKMNP-TV-Z]{26}$"  # hivemind.guard.GUARD_REPORT_ID_PATTERN.
MAX_PHRASE_CHARS = 64  # Far longer than any break-glass phrase; bounded like every field.

TargetPath = Annotated[
    str,
    Path(
        pattern=r"^(cell|lease)_[0-9A-HJKMNP-TV-Z]{26}$",
        description="The Cell's id, or the id of a lease on it.",
    ),
]

__all__ = ["MAX_PHRASE_CHARS", "ROUTES", "StingCutBody", "StingCutView", "sting_cut_view"]


class StingCutBody(BaseModel):
    """Cut one Cell off: why, the typed phrase, and the Guard report it answers, if any."""

    model_config = _CONFIG

    reason: str = Field(
        min_length=1,
        max_length=MAX_STING_CUT_REASON_CHARS,
        description="Why, in a short phrase for the trail (ids, not content).",
    )
    phrase: str = Field(
        min_length=1,
        max_length=MAX_PHRASE_CHARS,
        description="The break-glass phrase, typed: 'sting cut this cell'.",
    )
    report_id: str | None = Field(
        default=None, pattern=_REPORT_ID, description="A Guard report the Queen was sent."
    )


class StingCutView(BaseModel):
    """What the Sting Cut did: cut the Cell off, or found it cut already."""

    model_config = _CONFIG

    cell_id: str = Field(description="The Cell.")
    cut: bool = Field(description="This order cut it (cell.sting_cut recorded).")
    already_cut: bool = Field(description="It was cut before; nothing changed.")
    event_id: str | None = Field(description="The cell.sting_cut event, when cut now.")
    isolated_event_id: str | None = Field(description="The cell.isolated event it follows.")
    snapshot_id: str | None = Field(description="The snapshot kept as evidence, if any.")
    revoked_grant_ids: list[str] = Field(description="The grants on it revoked.")
    scrub: str = Field(description="confirmed, ordered, unconfirmed or unreached.")
    key_forgotten: bool = Field(description="Its key is forgotten; its Warden cannot redial.")
    torn_down: bool = Field(description="A Night Veil Cell torn down, its records purged.")


def sting_cut_view(outcome: StingCutOutcome) -> StingCutView:
    """Shape the Queen's Sting Cut outcome for the Landing Board.

    Args:
        outcome: What the one cut path did.

    Returns:
        The view.
    """
    return StingCutView(
        cell_id=outcome.cell_id,
        cut=outcome.cut,
        already_cut=outcome.already_cut,
        event_id=outcome.event_id,
        isolated_event_id=outcome.isolated_event_id,
        snapshot_id=outcome.snapshot_id,
        revoked_grant_ids=list(outcome.revoked_grant_ids),
        scrub=outcome.scrub.value,
        key_forgotten=outcome.key_forgotten,
        torn_down=outcome.torn_down,
    )


async def sting_cut(
    target: TargetPath, body: StingCutBody, caller: CallerParam, services: Services
) -> StingCutView:
    """Cut a Cell off on the human's order: interactive, after step-up, with the typed phrase.

    Args:
        target: The Cell, or a lease on it.
        body: Why, the phrase, and the Guard report it answers, if any.
        caller: The admitted caller: an interactive device inside its step-up window.
        services: The Entrance's services (the Queen's door).

    Returns:
        What the cut did, or that the Cell was cut already.
    """
    await require_step_up(services, caller, ActionKind.STING_CUT)
    check_break_glass(ActionKind.STING_CUT, caller.session, body.phrase)
    # One Queen call: her one cut path, bounded by its own scrub and snapshot bounds.
    outcome = await services.queen.sting_cut(target, caller.device.id, body.reason, body.report_id)
    return sting_cut_view(outcome)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path="/v1/cells/{target}/sting-cut",
        listeners=BOTH_LISTENERS,
        access=session_with(STEWARD),
        effect=RouteEffect.DOOR,
        endpoint=sting_cut,
        summary="Sting Cut a Cell (interactive, after step-up, with the typed phrase).",
        response_model=StingCutView,
    ),
)
