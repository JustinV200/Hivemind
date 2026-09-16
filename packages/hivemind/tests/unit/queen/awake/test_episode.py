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

from builders.memory import make_wax_proposal_input
from builders.queen import make_queen_deps
from builders.supervision import make_alarm

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import Effort
from hivemind.llm import FakeLLMProvider, ProviderCapabilities
from hivemind.llm.errors import ContextTooLongError
from hivemind.llm.fake import text_response
from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.memory import MemoryContext, TriggerEvent
from hivemind.memory.cell_wax import propose_wax, write_wax
from hivemind.memory.overflow import MAX_OVERFLOWS, ContextOverflowError
from hivemind.pheromone import TrailQuery
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.awake import QueenSources, decide_awake
from hivemind.queen.deps import MemoryBudget
from hivemind.queen.human_inbox import HumanInbox
from waggle.messages.cell.wax import WaxDecision

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


async def test_queen_sources_wax_returns_written_notes_only_for_cells_asked_for() -> None:
    """Roadmap step 4.2a: HotStateSources.wax returns nothing for a Cell not in `cells`."""
    deps, _link, _warden_end = make_queen_deps()
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    inputs = make_wax_proposal_input(clock=deps.clock)
    proposed = await propose_wax(inputs, 4_000, ctx)
    await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)
    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())

    in_play = await sources.wax(frozenset({inputs.cell_id}))
    not_in_play = await sources.wax(frozenset())

    assert len(in_play) == 1
    assert in_play[0].cell_id == inputs.cell_id
    assert not_in_play == ()


async def test_decide_awake_shrinks_and_retries_on_context_too_long() -> None:
    # Roadmap step 4.4: a ContextTooLongError never crashes the Queen's own awake episode.
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    provider.script(
        ContextTooLongError("fake", window=1_000, requested=2_000),
        text_response(json.dumps(_DECISION_JSON)),
    )
    deps, _link, _warden_end = make_queen_deps(fake_provider=provider)
    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
    event = TriggerEvent(
        kind="test.trigger", summary="Something happened.", clearance=HoneyClearance.C1
    )

    decision = await decide_awake(deps, event, sources, Effort.LOW)

    assert decision.action is QueenAction.RECORD
    events = await deps.trail.query(TrailQuery())
    assert [e.kind for e in events] == ["memory.overflow", "memory.episode"]


async def test_decide_awake_raises_context_overflow_after_max_overflows() -> None:
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    for _ in range(MAX_OVERFLOWS + 1):
        provider.script(ContextTooLongError("fake", window=1_000, requested=2_000))
    deps, _link, _warden_end = make_queen_deps(fake_provider=provider)
    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
    event = TriggerEvent(
        kind="test.trigger", summary="Something happened.", clearance=HoneyClearance.C1
    )

    try:
        await decide_awake(deps, event, sources, Effort.LOW)
    except ContextOverflowError as exc:
        assert exc.attempts == MAX_OVERFLOWS
    else:
        raise AssertionError("expected ContextOverflowError")


async def test_decide_awake_deposits_every_dropped_alarm_into_bee_bread() -> None:
    # Roadmap step 4.4: "every dropped item is findable in Bee Bread by id," proven through the
    # Queen's own awake episode, not only through hivemind.memory.assemble directly. A tiny
    # output_reserve budget (memory_budget below) leaves only a sliver of room for hot state, so
    # most of these 50 alarms must be dropped and archived.
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full(), responder=_responder)
    deps, _link, _warden_end = make_queen_deps(
        fake_provider=provider,
        memory_budget=MemoryBudget(budget_fraction=0.0005, output_reserve_tokens=1),
    )
    human_inbox = HumanInbox()
    for i in range(50):
        human_inbox.add_alarm(make_alarm(clock=deps.clock, detail=f"alarm {i}"))
    sources = QueenSources(deps.chamber, deps.memory, human_inbox)
    event = TriggerEvent(
        kind="test.trigger", summary="Something happened.", clearance=HoneyClearance.C1
    )

    await decide_awake(deps, event, sources, Effort.LOW)

    trail_kinds = [e.kind for e in await deps.trail.query(TrailQuery())]
    assert "memory.bee_bread_deposited" in trail_kinds
