"""Ask the Queen for a goal as a durable goal request, and wait until she has planned it.

Roadmap step 10.3c (ADR-0039): only a structured goal request initiates Night Veil work, so a
tier named on the local CLI (`hive run --comb-shield night_veil`) is recorded exactly the way the
Hive Entrance records a device's request: a `GoalRequest` (origin HUMAN, the tier asked for, no
device and so no device ceiling) committed through `Queen.request_goal`, which the Queen plans on
her own tick. `request_goal_and_wait` polls the row until it is PLANNED (its goal id is then what
`hive run` follows) or REFUSED, or the run's own timeout passes first; either of the last two
ends in `GoalNotPlannedError`, which `hive run` prints as a refusal or a timeout, never as a
failure of the Hive itself.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.run_requested_goal`. Calls into `hivemind.cell`,
    `hivemind.common.errors`, `hivemind.queen` (Queen), `hivemind.queen.intake` and waggle only.

Key invariants:
    - The request carries the operator's own terms and nothing else: origin HUMAN, no device,
      no capability ceiling (the operator's local path has none), so it is planned exactly as a
      goal the operator submitted, with the tier fixed by the human rather than the planner.
    - The wait never outlives the run's own deadline, planning time included.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md for the goal request.
    - hivemind.queen.intake for GoalRequest and its states.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from hivemind.cell import CombShieldLevel, HoneyClearance, RequestOrigin
from hivemind.common.errors import HiveMindError
from hivemind.queen import Queen
from hivemind.queen.intake import (
    GoalRequest,
    GoalRequestState,
    GoalRequestStore,
    new_goal_request_id,
)
from waggle.clock import Clock
from waggle.ids import TaskId

_POLL_INTERVAL_S = 0.05  # The same gentle cadence `run_goal` polls its tasks at.

__all__ = ["GoalAsk", "GoalNotPlannedError", "request_goal_and_wait"]


class GoalNotPlannedError(HiveMindError):
    """Raise when a requested goal was refused, or not planned before the run's deadline."""

    code: ClassVar[str] = "hivemind.cli.goal_not_planned"

    def __init__(self, request_id: str, refusal: str | None) -> None:
        """Build the error for one request that never became a goal.

        Args:
            request_id: The goal request's id.
            refusal: Why the Queen refused it, for the human; None when the deadline passed
                while it was still being planned.
        """
        why = refusal if refusal is not None else "it was not planned before the deadline"
        super().__init__(f"Goal request {request_id} was not planned: {why}")
        self.request_id = request_id
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class GoalAsk:
    """One goal the operator asks for by request (codingrules 5.1's argument group).

    Attributes:
        text: The goal, in the operator's own words.
        clearance: The goal's data-sensitivity ceiling.
        comb_shield: The tier the operator named; every planned task needs it.
    """

    text: str
    clearance: HoneyClearance
    comb_shield: CombShieldLevel


async def request_goal_and_wait(
    queen: Queen, requests: GoalRequestStore, clock: Clock, ask: GoalAsk, deadline_s: float
) -> TaskId:
    """Commit `ask` as a goal request, then wait for the Queen to plan it.

    Args:
        queen: The running Queen; `request_goal` commits the row and wakes her.
        requests: Her goal-request table, read to follow the row.
        clock: The run's clock: mints the id and timestamps, and paces the wait.
        ask: The goal and its terms.
        deadline_s: The run's deadline on `clock.monotonic()`; the wait never passes it.

    Returns:
        The planned goal's id.

    Raises:
        GoalNotPlannedError: The Queen refused the request, or the deadline passed first.
    """
    now = clock.now()
    request = GoalRequest(
        id=new_goal_request_id(clock),
        text=ask.text,
        clearance=ask.clearance,
        comb_shield=ask.comb_shield,
        origin=RequestOrigin.HUMAN,  # The operator at the terminal is the human asking.
        received_at=now,
        updated_at=now,
    )
    request_id = await queen.request_goal(request)
    while True:
        row = await requests.get(request_id)
        if row.state is GoalRequestState.PLANNED and row.goal_id is not None:
            return row.goal_id
        if row.state is GoalRequestState.REFUSED:
            raise GoalNotPlannedError(request_id, row.refusal)
        if clock.monotonic() >= deadline_s:
            raise GoalNotPlannedError(request_id, None)
        # External wait: the Queen plans beside her own tick; paced on the injected clock.
        await clock.sleep(_POLL_INTERVAL_S)
