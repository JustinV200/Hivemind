"""Tests for hivemind.wardens.awake.episode: decide_awake at full and zero capabilities.

Fits into the Hive:
    Mirrors src/hivemind/wardens/awake/episode.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.awake.episode for the module under test.
    - hivemind.llm.capabilities for ProviderCapabilities.none(): a plain-text model still owes a
      decision, on the PROMPTED rung of the structured-output ladder.
"""

from __future__ import annotations

import json

from builders.wardens import make_warden_deps

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider, ProviderCapabilities, text_response
from hivemind.memory import (
    AlarmSummary,
    DecisionSummary,
    Note,
    Pin,
    QuestionSummary,
    TaskSummary,
    TriggerEvent,
)
from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.awake import decide_awake


class _EmptyHotState:
    """A HotStateSources with nothing in it, so assemble() packs only the triggering event."""

    async def active_tasks(self) -> tuple[TaskSummary, ...]:
        return ()

    async def open_alarms(self) -> tuple[AlarmSummary, ...]:
        return ()

    async def pending_questions(self) -> tuple[QuestionSummary, ...]:
        return ()

    async def recent_decisions(self, limit: int) -> tuple[DecisionSummary, ...]:
        return ()

    async def pins(self) -> tuple[Pin, ...]:
        return ()

    async def notes(self) -> tuple[Note, ...]:
        return ()


def _event() -> TriggerEvent:
    return TriggerEvent(
        kind="test.trigger",
        summary="something happened",
        payload_ref=None,
        clearance=HoneyClearance.C1,
    )


def _decision_json() -> str:
    return json.dumps({"action": "RECORD", "reason": "nothing needs doing", "binding": None})


async def test_decide_awake_returns_a_decision_at_full_capabilities() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    provider.script(text_response(_decision_json()))
    deps, _queen_end, _warden_id = make_warden_deps(fake_provider=provider)

    decision = await decide_awake(deps, _event(), _EmptyHotState())

    assert decision.action is WardenAction.RECORD
    assert decision.reason == "nothing needs doing"
    assert len(provider.calls) == 1


async def test_decide_awake_still_yields_a_decision_at_zero_capabilities() -> None:
    # ProviderCapabilities.none() forces the PROMPTED rung: the reply must be a fenced ```json
    # block, not raw JSON (hivemind.llm.ladders.extraction.extract_json_block's own contract).
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.none())
    provider.script(text_response(f"```json\n{_decision_json()}\n```"))
    deps, _queen_end, _warden_id = make_warden_deps(fake_provider=provider)

    decision = await decide_awake(deps, _event(), _EmptyHotState())

    assert decision.action is WardenAction.RECORD


async def test_decide_awake_records_an_episode_in_memory() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    provider.script(text_response(_decision_json()))
    deps, _queen_end, _warden_id = make_warden_deps(fake_provider=provider)

    await decide_awake(deps, _event(), _EmptyHotState())

    episodes = await deps.memory.list_episodes(None, HoneyClearance.C1, 10)
    assert len(episodes) == 1
    assert episodes[0].is_autopilot is False
    assert episodes[0].decision == "RECORD"
