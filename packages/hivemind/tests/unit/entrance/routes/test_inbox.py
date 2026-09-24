"""Test hivemind.entrance.routes.inbox with the push channel: a question, its notices, its answer.

Roadmap step 10.5b's scenario over real listeners and a real Queen: a Warden's question the Queen
routes to the human reaches a program's webhook and a phone's Web Push subscription (through a
fake push service), the program answers it through the Landing Board, the Queen forwards the answer
to the Warden, and the Web Push copy of the notice is withdrawn.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving
from builders.human import single_task_plan
from builders.queen import plan_responder
from unit.entrance.push.support import HOOK_HOST, HOOK_URL, PUSH_HOST, PUSH_URL, UserAgent

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.entrance.push import NoticeKind
from hivemind.llm import FakeLLMProvider
from waggle.ids import new_message_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Question

_LISTENING = ProgramGrant(capabilities=("observe", "entrance:answer", "entrance:push"))
_WAIT_S = 5.0  # Generous: every step here is local.


async def _eventually(check: Callable[[], bool | Awaitable[bool]]) -> None:
    """Wait until ``check`` holds, polling briefly; fail after ``_WAIT_S``."""
    async with asyncio.timeout(_WAIT_S):
        while True:
            held = check()
            if held if isinstance(held, bool) else await held:
                return
            await asyncio.sleep(0.01)


def _to(rig: ServingRig, host: str) -> list[httpx.Request]:
    """Every push delivery the fake service received for ``host`` (pinned: named in ``Host``)."""
    return [request for request in rig.push_service.requests if request.headers["host"] == host]


async def _ask(rig: ServingRig) -> str:
    """Plan a goal, have its Warden ask the human a question, and return the chamber's id."""
    goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await rig.warden_end.wait_for_assignment()
    question = Question(
        question_id=new_message_id(rig.clock),
        task_id=goal_id,
        asked_by=rig.queen.wardens[0].warden_id,
        text="Which environment should the haiku target?",
        options=("staging", "prod"),
        clearance=WireHoneyClearance.C1,
        asked_at=rig.clock.now(),
    )
    await rig.warden_end.send(question)

    async def blocked() -> bool:
        return (await rig.deps.chamber.get(goal_id)).status is TaskStatus.BLOCKED

    await _eventually(blocked)
    [pending] = await rig.queen.human_inbox.pending_questions(rig.deps.chamber)
    return pending.id


async def test_a_question_reaches_both_channels_and_an_api_answer_withdraws_it() -> None:
    agent = UserAgent()
    provider = FakeLLMProvider(responder=plan_responder(single_task_plan))
    async with serving(RigOptions(provider=provider)) as rig:
        hook, hook_session = await rig.program(_LISTENING)
        phone, phone_session = await rig.program(_LISTENING)
        subscribed = [
            await hook.call(
                hook_session,
                "POST",
                "/v1/push/subscriptions",
                {"channel": "webhook", "endpoint": HOOK_URL},
            ),
            await phone.call(
                phone_session,
                "POST",
                "/v1/push/subscriptions",
                {"channel": "web_push", "endpoint": PUSH_URL, "keys": agent.keys.model_dump()},
            ),
        ]
        running = asyncio.ensure_future(rig.queen.run())
        try:
            question_id = await _ask(rig)
            await _eventually(lambda: bool(_to(rig, HOOK_HOST)) and bool(_to(rig, PUSH_HOST)))
            answered = await hook.call(
                hook_session,
                "POST",
                f"/v1/inbox/questions/{question_id}/answer",
                {"text": "staging", "chosen_option": 0},
            )
            forwarded = await rig.warden_end.wait_for_answer()
            await _eventually(lambda: len(_to(rig, PUSH_HOST)) >= 2)
        finally:
            await rig.queen.stop()
            await running

    assert [response.status_code for response in subscribed] == [201, 201]
    webhook_notice = json.loads(_to(rig, HOOK_HOST)[0].content)
    assert (webhook_notice["kind"], webhook_notice["ref"]) == ("question_waiting", question_id)
    assert "X-Hive-Signature" in _to(rig, HOOK_HOST)[0].headers
    assert answered.status_code == 200, answered.text
    assert answered.json()["question_status"] == "ANSWERED"
    assert forwarded.text == "staging"
    first, withdrawal = (
        json.loads(agent.open(request.content)) for request in _to(rig, PUSH_HOST)[:2]
    )
    assert (first["kind"], first["ref"]) == (NoticeKind.QUESTION_WAITING.value, question_id)
    assert (withdrawal["kind"], withdrawal["ref"]) == (NoticeKind.WITHDRAWN.value, question_id)
