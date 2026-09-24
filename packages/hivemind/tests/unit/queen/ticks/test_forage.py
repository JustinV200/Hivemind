"""Tests for hivemind.queen.ticks.forage: handle_forage_request.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/forage.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.forage for the module under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from builders.forage import make_capacity, make_grant
from builders.queen import make_queen_deps

from hivemind.forage.grant_state import GrantState
from hivemind.llm import FakeLLMProvider
from hivemind.llm.fake import text_response
from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.ticks.forage import handle_forage_request
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_message_id
from waggle.messages.forage import ForageDelta, ForageOutcome
from waggle.messages.forage import ForageRequest as WireForageRequest
from waggle.messages.forage.values import ForageRequestKind as WireForageRequestKind
from waggle.messages.labels import AccuracyBar as WireAccuracyBar
from waggle.messages.labels import Tempo as WireTempo

_EMPTY_DELTA_FIELDS = {
    "seats": 0,
    "source_id": None,
    "spend": 0.0,
    "tokens": 0,
    "slot": None,
    "minimum_grade": None,
}


def _wire_request(
    grant_id: str, sub_bees: int = 1, kind: WireForageRequestKind = WireForageRequestKind.SUB_BEES
) -> WireForageRequest:
    return WireForageRequest(
        grant_id=grant_id,
        kind=kind,
        wanted=ForageDelta(sub_bees=sub_bees, **_EMPTY_DELTA_FIELDS),
        task_id=None,
        tempo=WireTempo(latency_budget_s=None, accuracy=WireAccuracyBar.NORMAL),
        reason="a test wants more sub-bees",
    )


def _json_responder(decision: dict[str, object]) -> Callable[[LLMRequest], LLMResponse]:
    """Build a FakeLLMProvider responder answering `decision` on either structured-output rung."""

    def responder(request: LLMRequest) -> LLMResponse:
        if request.response_schema is not None:
            return text_response(json.dumps(decision))
        return text_response(f"```json\n{json.dumps(decision)}\n```")

    return responder


async def test_a_request_within_headroom_grants_at_once_with_a_wire_grant_and_reply() -> None:
    deps, link, warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=10))
    grant = make_grant(
        clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2
    )
    await deps.ledger.record_grant(grant)
    request = _wire_request(grant.id, sub_bees=3)

    await handle_forage_request(
        deps, {link.warden_id: link}, link, request, new_message_id(deps.clock)
    )

    wire_grant = await warden_end.wait_for_grant()
    assert wire_grant.max_sub_bees == 5  # 2 already held + 3 wanted.
    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.GRANTED
    assert reply.granted.sub_bees == 3  # Roadmap 4.7's leftover: the delta matches the kind.
    assert reply.revision == wire_grant.revision
    events = await deps.trail.query(TrailQuery())
    assert [e.kind for e in events] == ["forage.requested", "forage.granted"]


async def test_a_request_over_capacity_is_denied_with_a_reason() -> None:
    deps, link, warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=2))
    grant = make_grant(
        clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2
    )
    await deps.ledger.record_grant(grant)
    request = _wire_request(grant.id, sub_bees=10)

    await handle_forage_request(
        deps, {link.warden_id: link}, link, request, new_message_id(deps.clock)
    )

    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.DENIED
    assert reply.revision is None
    assert reply.expires_at is None
    assert warden_end.grants == []
    events = await deps.trail.query(TrailQuery())
    assert [e.kind for e in events] == ["forage.requested", "forage.denied"]
    assert events[-1].payload["contested"] is False
    # This dispatch's own fix: the trail's own forage.denied payload carries the same reason the
    # wire ForageReply does, so "denied with a reason on the trail" (roadmap 4.8) actually holds.
    assert events[-1].payload["reason"] == reply.reason


async def test_a_contested_request_denied_by_the_awake_episode_records_the_effort_and_reason() -> (
    None
):
    clock = FakeClock()
    reason = "No other grant has enough spare capacity worth shrinking."
    decision_json: dict[str, object] = {
        "action": "DENY_REQUEST",
        "task_id": None,
        "reason": reason,
        "binding": None,
        "shrink_grant_id": None,
        "shrink_amount": None,
    }
    provider = FakeLLMProvider(name="fake", responder=_json_responder(decision_json))
    deps, link, warden_end = make_queen_deps(clock=clock, fake_provider=provider)
    await deps.ledger.report_capacity(new_cell_id(clock), make_capacity(max_sub_bees=4))
    mine = make_grant(clock=clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2)
    other = make_grant(clock=clock, state=GrantState.ACTIVE, max_sub_bees=2)
    await deps.ledger.record_grant(mine)
    await deps.ledger.record_grant(other)
    request = _wire_request(mine.id, sub_bees=1)

    await handle_forage_request(
        deps, {link.warden_id: link}, link, request, new_message_id(deps.clock)
    )

    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.DENIED
    assert reply.reason == reason
    events = await deps.trail.query(TrailQuery())
    assert events[-1].kind == "forage.denied"
    assert events[-1].payload["contested"] is True
    assert events[-1].payload["effort"] == "HIGH"
    assert events[-1].payload["reason"] == reason


async def test_a_contested_request_grants_by_shrinking_and_notifies_the_holder() -> None:
    # Roadmap step 4.7's leftover: GRANT_BY_SHRINKING shrinks the named grant first (whose own
    # Warden then notices it is over its new cap and raises GRANT_EXCEEDED --
    # tests/unit/wardens/test_warden_spawn_and_accept.py::
    # test_a_grant_shrunk_below_current_usage_raises_grant_exceeded already covers that
    # consequence directly against a live Warden), then grants the requester from the headroom
    # the shrink freed.
    clock = FakeClock()
    other_grant_id = new_grant_id(clock)
    provider = _shrink_decision_provider(other_grant_id)
    deps, link, warden_end = make_queen_deps(clock=clock, fake_provider=provider)
    await deps.ledger.report_capacity(new_cell_id(clock), make_capacity(max_sub_bees=4))
    mine = make_grant(clock=clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2)
    other = make_grant(
        clock=clock,
        id=other_grant_id,
        holder=link.warden_id,
        state=GrantState.ACTIVE,
        max_sub_bees=2,
    )
    await deps.ledger.record_grant(mine)
    await deps.ledger.record_grant(other)
    request = _wire_request(mine.id, sub_bees=1)

    await handle_forage_request(
        deps, {link.warden_id: link}, link, request, new_message_id(deps.clock)
    )

    await warden_end.pump_until(lambda: len(warden_end.grants) >= 2)
    max_sub_bees_seen = sorted(g.max_sub_bees for g in warden_end.grants)
    assert max_sub_bees_seen == [1, 3]  # other shrunk 2 -> 1; mine grown 2 -> 1 + wanted 1 = 3.
    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.GRANTED
    assert reply.granted.sub_bees == 1  # The delta matches the kind (roadmap 4.7's leftover).
    shrunk_grant = deps.ledger.grant(other_grant_id)
    grown_grant = deps.ledger.grant(mine.id)
    assert shrunk_grant is not None and shrunk_grant.max_sub_bees == 1
    assert grown_grant is not None and grown_grant.max_sub_bees == 3
    events = await deps.trail.query(TrailQuery())
    assert [e.kind for e in events] == [
        "forage.requested",
        "memory.episode",  # decide_awake's own episode record (codingrules 12).
        "forage.granted",
        "forage.granted",
    ]


def _shrink_decision_provider(other_grant_id: str) -> FakeLLMProvider:
    """Script the awake episode to answer GRANT_BY_SHRINKING one seat off `other_grant_id`."""
    decision_json: dict[str, object] = {
        "action": "GRANT_BY_SHRINKING",
        "task_id": None,
        "reason": "Shrinking the other grant frees enough headroom.",
        "binding": None,
        "shrink_grant_id": other_grant_id,
        "shrink_amount": 1,
    }
    return FakeLLMProvider(name="fake", responder=_json_responder(decision_json))


async def test_a_warden_asking_to_grow_a_grant_it_does_not_hold_is_refused() -> None:
    # Roadmap step 10.3: before it, the grant was found by id alone, so any Warden could grow any.
    deps, link, warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=10))
    someone_elses = make_grant(clock=deps.clock, state=GrantState.ACTIVE, max_sub_bees=2)
    await deps.ledger.record_grant(someone_elses)

    await handle_forage_request(
        deps,
        {link.warden_id: link},
        link,
        _wire_request(someone_elses.id, sub_bees=3),
        new_message_id(deps.clock),
    )

    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.DENIED
    assert warden_end.grants == []
    assert deps.ledger.grant(someone_elses.id) == someone_elses
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["rule"] == "guard.scope.grant_holder"
    assert denial.payload["point"] == "forage_request"
    assert denial.subject_id == link.warden_id
