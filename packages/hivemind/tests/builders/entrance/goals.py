"""Script a served Hive's model calls so a goal given through the Entrance runs to its end.

A test that submits a goal through the Landing Board (``hive run --remote``, the goal follower)
needs the Hive behind it to do the goal for real: the Queen plans it, a Drone on the Hive Stand's
own Cell writes a file, the judge approves, and the goal finishes. ``goal_responder`` scripts that
through ``hive serve``'s one fake provider, on whichever structured-output rung each call is on;
with ``question`` the Drone first asks the human that question (the ``ask`` tool), so the task
blocks until someone answers through ``hive inbox --remote``. An awake episode of the Queen (a
chat message she answers) replies with ``REPLY_TEXT``.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used with
    ``builders.entrance.stand.serving_stand`` by the remote CLI's tests and the end-to-end test
    of phase 10's second exit criterion.

Key invariants:
    - Every model id here is neutral; nothing names a real vendor model.
"""

from __future__ import annotations

import json

from e2e.kernel_helpers import (
    default_worker_turn,
    judge_approve_response,
    plan_response,
    single_task_plan,
    text_response,
    tool_response,
    tool_round_count,
    write_call,
)

from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse, Responder

GOAL_FILE = "haiku_1.txt"  # The one file the scripted Drone writes.
REPLY_TEXT = "The haiku is written."  # What the Queen says in the chat when asked.
_PLANNER_MARK = "<<<user>>>"  # Only the planner's prompt carries the goal in a user section.

__all__ = ["GOAL_FILE", "REPLY_TEXT", "goal_responder"]


def goal_responder(question: str | None = None) -> Responder:
    """Script a goal that runs to its end, asking the human ``question`` first when given.

    Args:
        question: What the Drone asks the human before it writes; None asks nothing.

    Returns:
        The responder for ``serving_stand``.
    """

    def respond(request: LLMRequest) -> LLMResponse:
        """Answer one model call by its slot."""
        if request.slot is ModelSlot.QUEEN:
            return _queen(request)
        if request.slot is ModelSlot.WORKER:
            return _worker(request, question)
        if request.slot is ModelSlot.JUDGE:
            return judge_approve_response(request)
        return text_response("{}")

    return respond


def _queen(request: LLMRequest) -> LLMResponse:
    """Plan the goal as one task, or reply in the chat from an awake episode."""
    if _PLANNER_MARK in (request.system or ""):
        return plan_response(request, single_task_plan(GOAL_FILE))
    decision = {"action": "REPLY", "reason": "The human asked.", "message": REPLY_TEXT}
    return text_response(json.dumps(decision))


def _worker(request: LLMRequest, question: str | None) -> LLMResponse:
    """Ask first when scripted to, then write the file, then stop."""
    if question is None:
        return default_worker_turn(request, (GOAL_FILE,))
    rounds = tool_round_count(request)
    if rounds == 0:
        return tool_response(request, (("ask_1", "ask", {"text": question}),))
    if rounds == 1:
        return tool_response(request, (write_call(GOAL_FILE),))
    return text_response("The haiku is written.")
