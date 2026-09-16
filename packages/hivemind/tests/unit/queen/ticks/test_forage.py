"""Tests for hivemind.queen.ticks.forage: handle_forage_request.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/forage.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.forage for the module under test.
"""

from __future__ import annotations

from builders.forage import make_capacity, make_grant
from builders.queen import make_queen_deps

from hivemind.forage.grant_state import GrantState
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.ticks.forage import handle_forage_request
from waggle.ids import new_cell_id, new_message_id
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


async def test_a_request_within_headroom_grants_at_once_with_a_wire_grant_and_reply() -> None:
    deps, link, warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=10))
    grant = make_grant(
        clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2
    )
    await deps.ledger.record_grant(grant)
    request = _wire_request(grant.id, sub_bees=3)

    await handle_forage_request(deps, link, request, new_message_id(deps.clock))

    wire_grant = await warden_end.wait_for_grant()
    assert wire_grant.max_sub_bees == 5  # 2 already held + 3 wanted.
    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.GRANTED
    assert reply.granted.sub_bees == 3
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

    await handle_forage_request(deps, link, request, new_message_id(deps.clock))

    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.DENIED
    assert reply.revision is None
    assert reply.expires_at is None
    assert warden_end.grants == []
    events = await deps.trail.query(TrailQuery())
    assert [e.kind for e in events] == ["forage.requested", "forage.denied"]
    assert events[-1].payload["contested"] is False


async def test_a_contested_request_is_recorded_with_the_effort_class_and_denied() -> None:
    deps, link, warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=4))
    mine = make_grant(
        clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2
    )
    other = make_grant(clock=deps.clock, state=GrantState.ACTIVE, max_sub_bees=2)
    await deps.ledger.record_grant(mine)
    await deps.ledger.record_grant(other)
    request = _wire_request(mine.id, sub_bees=1)

    await handle_forage_request(deps, link, request, new_message_id(deps.clock))

    reply = await warden_end.wait_for_forage_reply()
    assert reply.outcome is ForageOutcome.DENIED
    events = await deps.trail.query(TrailQuery())
    assert events[-1].kind == "forage.denied"
    assert events[-1].payload["contested"] is True
    assert events[-1].payload["effort"] == "HIGH"
