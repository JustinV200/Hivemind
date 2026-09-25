"""Provide the Queen's durable goal requests: a goal is written down before it is acknowledged.

Docs/adr/0040, "A goal is durable before it is acknowledged": the Hive Entrance hands a goal to
`Queen.request_goal`, which commits a `GoalRequest` row (with its trail event, in one transaction)
and wakes the Queen, and only then does the Entrance answer `202`. The Queen plans every RECEIVED
request herself on her own tick (`hivemind.queen.ticks.human.intake`): PLANNING, then
`submit_goal`'s plan-and-persist half with the request's budget, tier, origin, device, ceiling and
id, then PLANNED with the goal id, or REFUSED with the reason. A spoken goal (or one held for a
step-up) waits in AWAITING_CONFIRMATION until the human confirms it. On a restart, a PLANNING row
whose goal already exists in the Brood Chamber (found by the request id every task carries) becomes
PLANNED, and one whose goal does not is planned again, so a crash after the `202` loses nothing and
never plans a goal twice. Planning is never a task the Entrance owns, so reducing the Entrance
cannot kill it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Written
    by `Queen.request_goal`/`confirm_goal_request`/`decline_goal_request` and the intake drain;
    read by the drain and the Hive Entrance's goals views. Calls into `hivemind.brood_chamber`,
    `hivemind.cell`, `hivemind.common`, `hivemind.forage`, `hivemind.pheromone`,
    `hivemind.queen.errors`, `hivemind.queen.trail` and waggle only.

Key invariants:
    - Every write goes through `writes`, which checks the edge against `state`'s one table and
      commits the row with its `queen.goal_request_*` event together.
    - Nothing here ever puts a request's or a refusal's words on the trail or in a log.

See Also:
    - docs/adr/0040-hive-entrance-http-websocket-api-and-human-inbox.md for the decision.
    - .claude/codingrules.md Appendix C for the "Goal request" state machine row.
    - hivemind.queen.ticks.human.intake for the drain that plans requests.
    - hivemind.queen.chat for the Queen's human-facing door and the chat log.

Public API (roadmap step 10.5):
    - GoalRequest, GoalSource, MAX_GOAL_TEXT_CHARS, MAX_REFUSAL_CHARS: one request (model).
    - GoalRequestId, new_goal_request_id, GOAL_REQUEST_ID_PATTERN: its id, minted beside the
      Brood Chamber's task model because every task of the goal stores it (re-exported).
    - GoalRequestState, GOAL_REQUEST_TRANSITIONS, TERMINAL_GOAL_REQUEST_STATES,
      can_goal_request_transition, assert_goal_request_transition: its state machine (state).
    - GoalRequestNotFoundError, GoalRequestExistsError, InvalidGoalRequestTransitionError: the
      three ways its own bookkeeping refuses a caller (errors).
    - GoalRequestStore, GoalRequestQuery, check_goal_request_event, DEFAULT_REQUEST_PAGE,
      MAX_REQUEST_PAGE: the persistence seam (protocol).
    - InMemoryGoalRequestStore (memory), SqliteGoalRequestStore, apply_intake_migrations,
      SUBSYSTEM, MIGRATIONS_PACKAGE (sqlite): its two implementations.
    - receive, hold, confirm, decline, start_planning, mark_planned, refuse, mark_finished,
      Refusal, DECLINED_CODE: every write, each with its own event (writes).
    - goal_spend_cap, goal_budgets: a request's budget applied to its goal's grants (budget).
"""

from hivemind.brood_chamber.task import GOAL_REQUEST_ID_PATTERN, GoalRequestId, new_goal_request_id
from hivemind.queen.intake.budget import goal_budgets, goal_spend_cap
from hivemind.queen.intake.errors import (
    GoalRequestExistsError,
    GoalRequestNotFoundError,
    InvalidGoalRequestTransitionError,
)
from hivemind.queen.intake.memory import InMemoryGoalRequestStore
from hivemind.queen.intake.model import (
    MAX_GOAL_TEXT_CHARS,
    MAX_REFUSAL_CHARS,
    GoalRequest,
    GoalSource,
)
from hivemind.queen.intake.protocol import (
    DEFAULT_REQUEST_PAGE,
    MAX_REQUEST_PAGE,
    GoalRequestQuery,
    GoalRequestStore,
    check_goal_request_event,
)
from hivemind.queen.intake.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteGoalRequestStore,
    apply_intake_migrations,
)
from hivemind.queen.intake.state import (
    GOAL_REQUEST_TRANSITIONS,
    TERMINAL_GOAL_REQUEST_STATES,
    GoalRequestState,
    assert_goal_request_transition,
    can_goal_request_transition,
)
from hivemind.queen.intake.writes import (
    DECLINED_CODE,
    Refusal,
    confirm,
    decline,
    hold,
    mark_finished,
    mark_planned,
    receive,
    refuse,
    start_planning,
)

__all__ = [
    "DECLINED_CODE",
    "DEFAULT_REQUEST_PAGE",
    "GOAL_REQUEST_ID_PATTERN",
    "GOAL_REQUEST_TRANSITIONS",
    "MAX_GOAL_TEXT_CHARS",
    "MAX_REFUSAL_CHARS",
    "MAX_REQUEST_PAGE",
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "TERMINAL_GOAL_REQUEST_STATES",
    "GoalRequest",
    "GoalRequestExistsError",
    "GoalRequestId",
    "GoalRequestNotFoundError",
    "GoalRequestQuery",
    "GoalRequestState",
    "GoalRequestStore",
    "GoalSource",
    "InMemoryGoalRequestStore",
    "InvalidGoalRequestTransitionError",
    "Refusal",
    "SqliteGoalRequestStore",
    "apply_intake_migrations",
    "assert_goal_request_transition",
    "can_goal_request_transition",
    "check_goal_request_event",
    "confirm",
    "decline",
    "goal_budgets",
    "goal_spend_cap",
    "hold",
    "mark_finished",
    "mark_planned",
    "new_goal_request_id",
    "receive",
    "refuse",
    "start_planning",
]
