"""Tests for hivemind.workers.roles.guard_bee.judge: one awake episode over one finding.

The episode runs on the judge slot and is shown the finding's facts only (ids, counts, kinds, the
rule's verdict, the allowed actions), never a payload's words. A reply may raise or lower the
confidence and pick an allowed action; a reply outside the allowed actions, a provider outage, a
timeout or an unbound slot all return nothing, so the rule's own verdict stands.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/judge.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.prompts.guard_review for the prompt the episode renders.
    - tests.unit.workers.roles.guard_bee.test_bee for episodes changing a filed report.
"""

from __future__ import annotations

import asyncio

from builders.guard_bee import judge_reply
from builders.llm import make_bound

from hivemind.forage import ModelSlot
from hivemind.guard import GuardAction, GuardConfidence
from hivemind.llm import (
    BoundModel,
    DirectCallGate,
    FakeLLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderUnavailableError,
)
from hivemind.llm.prompts import PromptName, SectionLabel, load_prompt
from hivemind.workers.roles.guard_bee import (
    Finding,
    GuardRule,
    JudgeCase,
    Mark,
    ModelGuardJudge,
    Sighting,
    TrailFact,
    Verdict,
    allowed_actions,
    targets_of,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id, new_task_id, new_worker_id

_CLOCK = FakeClock()  # Mints ids and times.
_WORDS = "Refused: the Worker asked for net:evil.example"  # A reason a payload could carry.


def _case() -> JudgeCase:
    """A denial-burst finding for one bee on one Cell, as evaluation would build it."""
    rule = GuardRule.model_validate(
        {
            "key": "denial_burst",
            "title": "a burst of Guard refusals for one bee",
            "counts": [{"kind": "guard.denied"}],
            "group_by": "bee",
            "window_s": 300.0,
            "threshold": 5,
            "confidence": "medium",
            "action": "quarantine_bee",
            "judgement": True,
        }
    )
    ids = {"bee": new_worker_id(_CLOCK), "task": new_task_id(_CLOCK), "cell": new_cell_id(_CLOCK)}
    facts = [
        TrailFact(new_event_id(_CLOCK), _CLOCK.now(), "guard.denied", "node_x", ids, {"r": _WORDS})
        for _ in range(5)
    ]
    sightings = tuple(Sighting(fact=fact, ids=ids) for fact in facts)
    finding = Finding(rule, ids["bee"], sightings, 5.0, Mark(_CLOCK.now()))
    return JudgeCase(finding=finding, allowed=allowed_actions(targets_of(finding)))


def _judge(
    provider: FakeLLMProvider, gate: DirectCallGate | None = None, timeout_s: float = 5.0
) -> ModelGuardJudge:
    bound = make_bound(slot=ModelSlot.JUDGE, binding="judge", provider=provider)
    return ModelGuardJudge(lambda: bound, gate or DirectCallGate(), timeout_s)


async def test_the_episode_is_shown_the_findings_facts_and_never_a_payloads_words() -> None:
    provider = FakeLLMProvider(name="judge")
    provider.script(judge_reply("high", "quarantine_bee"))
    case = _case()

    await _judge(provider).judge(case)

    [request] = provider.calls
    system = request.system or ""
    assert request.slot is ModelSlot.JUDGE
    assert system.startswith(load_prompt(PromptName.GUARD_REVIEW).rstrip("\n"))
    assert f"<<<{SectionLabel.EVENT.value}>>>" in system
    assert case.finding.key in system and "guard.denied x5" in system
    assert "Allowed actions: isolate_cell, observe, quarantine_bee, sting_cut" in system
    assert _WORDS not in system


async def test_a_reply_may_raise_the_confidence_and_pick_an_allowed_action() -> None:
    provider = FakeLLMProvider(name="judge")
    provider.script(judge_reply("critical", "isolate_cell"))

    verdict = await _judge(provider).judge(_case())

    assert verdict == Verdict(GuardConfidence.CRITICAL, GuardAction.ISOLATE_CELL, judged=True)


async def test_a_reply_outside_the_allowed_actions_leaves_the_rules_verdict() -> None:
    provider = FakeLLMProvider(name="judge")
    provider.script(judge_reply("high", "reduce_entrance"))  # A narrowing: a rule's alone.

    assert await _judge(provider).judge(_case()) is None


async def test_a_provider_outage_leaves_the_rules_verdict() -> None:
    provider = FakeLLMProvider(name="judge")
    provider.script(ProviderUnavailableError("judge", "down for the test"))

    assert await _judge(provider).judge(_case()) is None


async def test_an_episode_past_its_timeout_leaves_the_rules_verdict() -> None:
    class _StuckGate(DirectCallGate):
        async def complete(self, bound: BoundModel, request: LLMRequest) -> LLMResponse:
            await asyncio.Event().wait()  # Never answers; only the judge's timeout ends it.
            raise AssertionError("unreachable")

    judge = _judge(FakeLLMProvider(name="judge"), gate=_StuckGate(), timeout_s=0.05)

    assert await judge.judge(_case()) is None


async def test_an_unbound_judge_slot_leaves_the_rules_verdict() -> None:
    judge = ModelGuardJudge(lambda: None, DirectCallGate(), 5.0)

    assert await judge.judge(_case()) is None
