"""Build the Queen's human end for tests: goal requests, a recording HumanChannel, scripted replies.

Roadmap step 10.5 (ADR-0040) gives the Queen a human end: durable goal requests
(`hivemind.queen.intake`), the chat log and its `HumanChannel` seam (`hivemind.queen.chat`).
`make_goal_request` builds a fresh RECEIVED `GoalRequest` over the test's own clock;
`RecordingHumanChannel` implements `HumanChannel` honestly and keeps every call it receives, in
order, so a test asserts exactly what the human's devices would have been told; `queen_responder`
builds one `FakeLLMProvider` responder for both of the Queen's own model calls (it answers a
planning call with a plan and an awake episode with a scripted decision, telling them apart by the
planner prompt's own `<<<user>>>` section) and keeps every request it answers, so a test can read
back exactly what an awake episode was shown (`prompt_text` flattens one request into the words
a model read); `single_task_plan` is the smallest valid plan; `wait_until` yields the event loop
until a condition holds.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/queen that drive the human end, and by the contract suites.

Key invariants:
    - Every builder that mints an id or a timestamp takes the test's own clock, so a run is
      deterministic.
    - `RecordingHumanChannel` never raises and returns at once, as the protocol requires.

See Also:
    - hivemind.queen.intake and hivemind.queen.chat for the code these builders feed.
    - builders.queen for make_queen_deps, the QueenDeps these are used with.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping

from hivemind.brood_chamber import QuestionStatus
from hivemind.cell import HoneyClearance
from hivemind.llm.fake import text_response
from hivemind.llm.models import LLMRequest, LLMResponse, TextPart
from hivemind.queen.chat import ChatEntry
from hivemind.queen.intake import GoalRequest, new_goal_request_id
from waggle.clock import Clock

DEFAULT_WAIT_TURNS = 400  # Generous: a stalled condition fails fast rather than hanging a test.
_PLANNER_TAG = "<<<user>>>"  # Only the planner's own rendered prompt carries the goal section.

__all__ = [
    "DEFAULT_WAIT_TURNS",
    "RecordingHumanChannel",
    "is_planning",
    "make_goal_request",
    "prompt_text",
    "queen_responder",
    "single_task_plan",
    "wait_until",
]


def make_goal_request(clock: Clock, **overrides: object) -> GoalRequest:
    """Build a fresh RECEIVED GoalRequest over `clock`, with any field overridden.

    Args:
        clock: The test's own clock; mints the id and both timestamps.
        **overrides: Any GoalRequest field to replace (text, comb_shield, budget_usd, ...).

    Returns:
        A validated GoalRequest, ready for `Queen.request_goal`.
    """
    fields: dict[str, object] = {
        "id": new_goal_request_id(clock),
        "text": "Write a haiku about bees.",
        "clearance": HoneyClearance.C1,
        "received_at": clock.now(),
        "updated_at": clock.now(),
    }
    fields.update(overrides)
    return GoalRequest.model_validate(fields)


def single_task_plan(goal: str) -> dict[str, object]:
    """Build the smallest valid plan: one task, one acceptance criterion, default needs."""
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": "done.txt", "argv": [], "expected": None}
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


def queen_responder(
    decision: Mapping[str, object],
    seen: list[LLMRequest],
    build_plan: Callable[[str], dict[str, object]] = single_task_plan,
) -> Callable[[LLMRequest], LLMResponse]:
    """Build one responder for the Queen's planner and her awake episodes, recording each request.

    Args:
        decision: The QueenDecision JSON every awake episode answers with.
        seen: Where every request answered is appended, in order.
        build_plan: Given the goal text, the plan a planning call answers with.

    Returns:
        A `hivemind.llm.Responder` for `FakeLLMProvider(responder=...)`.
    """

    def responder(request: LLMRequest) -> LLMResponse:
        seen.append(request)
        system = request.system or ""
        if not is_planning(request):
            return text_response(
                json.dumps(dict(decision))
            )  # An awake episode: its scripted decision.
        start = system.find(_PLANNER_TAG)
        end = system.find("<<<end user>>>")
        return text_response(json.dumps(build_plan(system[start + len(_PLANNER_TAG) : end])))

    return responder


def prompt_text(request: LLMRequest) -> str:
    """Return every word a model was shown in `request`: its system prompt, then each text part."""
    texts = [request.system or ""]
    for message in request.messages:
        texts.extend(part.text for part in message.parts if isinstance(part, TextPart))
    return "\n".join(texts)


def is_planning(request: LLMRequest) -> bool:
    """Return whether `request` is a planning call (the planner's own goal section), not awake."""
    return _PLANNER_TAG in (request.system or "")


class RecordingHumanChannel:
    """A HumanChannel that keeps every call, in order, as `(method name, argument)` pairs."""

    def __init__(self) -> None:
        """Start with no calls recorded."""
        self.calls: list[tuple[str, object]] = []

    def names(self) -> list[str]:
        """Return the method names called so far, in order."""
        return [name for name, _argument in self.calls]

    async def replied(self, entry: ChatEntry) -> None:
        """Record a reply (or a notice standing in for one)."""
        self.calls.append(("replied", entry))

    async def question_asked(self, entry: ChatEntry) -> None:
        """Record a question routed to the human."""
        self.calls.append(("question_asked", entry))

    async def question_closed(self, question_id: str, status: QuestionStatus) -> None:
        """Record a question answered or withdrawn."""
        self.calls.append(("question_closed", (question_id, status)))

    async def alarm_raised(self, entry: ChatEntry) -> None:
        """Record an Alarm that reached the human."""
        self.calls.append(("alarm_raised", entry))

    async def alarm_acknowledged(self, alarm_id: str) -> None:
        """Record an Alarm the human acknowledged."""
        self.calls.append(("alarm_acknowledged", alarm_id))

    async def goal_request_held(self, request: GoalRequest) -> None:
        """Record a request held for confirmation."""
        self.calls.append(("goal_request_held", request))

    async def goal_request_planned(self, request: GoalRequest) -> None:
        """Record a request planned."""
        self.calls.append(("goal_request_planned", request))

    async def goal_request_refused(self, request: GoalRequest) -> None:
        """Record a request refused."""
        self.calls.append(("goal_request_refused", request))

    async def goal_finished(self, request: GoalRequest) -> None:
        """Record a requested goal whose tasks are all terminal."""
        self.calls.append(("goal_finished", request))


async def wait_until(
    condition: Callable[[], Awaitable[bool]], turns: int = DEFAULT_WAIT_TURNS
) -> None:
    """Yield the event loop until `condition()` is True.

    Raises:
        AssertionError: It was still False after `turns` yields.
    """
    for _ in range(turns):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")
