"""Tests for hivemind.queen.awake.episode.decide_awake: one episode, full and zero capability.

Fits into the Hive:
    Mirrors src/hivemind/queen/awake/episode.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.awake.episode for the module under test.
"""

from __future__ import annotations

import json

from builders.queen import make_queen_deps

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import Effort
from hivemind.llm import FakeLLMProvider, ProviderCapabilities
from hivemind.llm.fake import text_response
from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.memory import TriggerEvent
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.awake import QueenSources, decide_awake
from hivemind.queen.human_inbox import HumanInbox

_DECISION_JSON = {"action": "RECORD", "task_id": None, "reason": "Nothing to do.", "binding": None}


def _responder(request: LLMRequest) -> LLMResponse:
    """Answer raw JSON on the native/json_mode rungs, fenced JSON on the PROMPTED rung.

    complete_structured only clears `response_schema` on its PROMPTED-rung request
    (hivemind.llm.ladders.structured._build_request_for_rung), so that field's presence is a
    reliable signal for which shape this fake should answer in.
    """
    if request.response_schema is not None:
        return text_response(json.dumps(_DECISION_JSON))
    return text_response(f"```json\n{json.dumps(_DECISION_JSON)}\n```")


async def test_decide_awake_yields_a_decision_at_full_capabilities() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full(), responder=_responder)
    deps, _link, _warden_end = make_queen_deps(fake_provider=provider)
    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
    event = TriggerEvent(
        kind="test.trigger", summary="Something happened.", clearance=HoneyClearance.C1
    )

    decision = await decide_awake(deps, event, sources, Effort.LOW)

    assert decision.action is QueenAction.RECORD


async def test_decide_awake_still_yields_a_decision_at_zero_capabilities() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none(), responder=_responder)
    deps, _link, _warden_end = make_queen_deps(fake_provider=provider)
    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
    event = TriggerEvent(
        kind="test.trigger", summary="Something happened.", clearance=HoneyClearance.C1
    )

    decision = await decide_awake(deps, event, sources, Effort.HIGH)

    assert decision.action is QueenAction.RECORD
