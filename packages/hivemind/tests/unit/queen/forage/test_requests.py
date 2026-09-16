"""Tests for hivemind.queen.forage.requests: handle_forage_request_for_kind.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/requests.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.requests for the module under test.
"""

from __future__ import annotations

from builders.forage import make_capacity, make_grant
from builders.queen import make_queen_deps

from hivemind.forage.grant_state import GrantState
from hivemind.forage.models.grants import SeatReservation
from hivemind.queen.autopilot import ForageAutopilotOutcome
from hivemind.queen.forage.requests import handle_forage_request_for_kind
from waggle.ids import new_cell_id, new_grant_id, new_task_id
from waggle.messages.forage import ForageDelta
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
    grant_id: str,
    sub_bees: int = 1,
    kind: WireForageRequestKind = WireForageRequestKind.SUB_BEES,
    task_id: str | None = None,
    **wanted_overrides: object,
) -> WireForageRequest:
    fields: dict[str, object] = dict(_EMPTY_DELTA_FIELDS)
    fields.update(wanted_overrides)
    return WireForageRequest(
        grant_id=grant_id,
        kind=kind,
        wanted=ForageDelta(sub_bees=sub_bees, **fields),
        task_id=task_id,
        tempo=WireTempo(latency_budget_s=None, accuracy=WireAccuracyBar.NORMAL),
        reason="a test wants more",
    )


async def test_denies_a_binding_kind_with_the_out_of_scope_reason() -> None:
    deps, link, _warden_end = make_queen_deps()
    grant = make_grant(clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE)
    await deps.ledger.record_grant(grant)
    request = _wire_request(grant.id, kind=WireForageRequestKind.BINDING)

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.DENY
    assert outcome.grant is None
    assert "routing" in outcome.reason


async def test_denies_a_grant_id_the_ledger_does_not_know() -> None:
    deps, _link, _warden_end = make_queen_deps()
    request = _wire_request(new_grant_id(deps.clock))

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.DENY
    assert outcome.grant is None
    assert "not known to the ledger" in outcome.reason


async def test_grants_a_request_within_headroom_and_grows_the_existing_grant() -> None:
    deps, link, _warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=10))
    grant = make_grant(
        clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2
    )
    await deps.ledger.record_grant(grant)
    request = _wire_request(grant.id, sub_bees=3)

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.GRANT
    assert outcome.grant is not None
    assert outcome.grant.max_sub_bees == 5  # 2 already held + 3 wanted.
    assert outcome.grant.revision == grant.revision + 1
    stored = deps.ledger.grant(grant.id)
    assert stored is not None
    assert stored.max_sub_bees == 5


async def test_denies_a_request_no_headroom_and_no_other_grant_can_cover() -> None:
    deps, link, _warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=2))
    grant = make_grant(
        clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2
    )
    await deps.ledger.record_grant(grant)
    request = _wire_request(grant.id, sub_bees=5)

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.DENY
    assert outcome.grant is None
    stored = deps.ledger.grant(grant.id)
    assert stored is not None
    assert stored.max_sub_bees == 2  # Unchanged.


async def test_needs_judgement_when_another_live_grant_could_be_shrunk() -> None:
    deps, link, _warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=4))
    mine = make_grant(
        clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, max_sub_bees=2
    )
    other = make_grant(clock=deps.clock, state=GrantState.ACTIVE, max_sub_bees=2)
    await deps.ledger.record_grant(mine)
    await deps.ledger.record_grant(other)
    # Headroom is 4 - 2 - 2 = 0 free; wants 1 more, which only shrinking `other` could cover.
    request = _wire_request(mine.id, sub_bees=1)

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.NEEDS_JUDGEMENT
    assert outcome.grant is None
    stored = deps.ledger.grant(mine.id)
    assert stored is not None
    assert stored.max_sub_bees == 2  # Never mutated on NEEDS_JUDGEMENT.


# ──────────────────────────────────────────────────────────────────────────────
# SHARED_SEATS (roadmap step 4.8)
# ──────────────────────────────────────────────────────────────────────────────


async def test_shared_seats_denies_a_request_with_no_source_id() -> None:
    deps, link, _warden_end = make_queen_deps()
    grant = make_grant(clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE)
    await deps.ledger.record_grant(grant)
    request = _wire_request(grant.id, kind=WireForageRequestKind.SHARED_SEATS, seats=2)

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.DENY
    assert "source_id" in outcome.reason


async def test_shared_seats_grants_within_headroom_and_adds_a_seat_reservation() -> None:
    deps, link, _warden_end = make_queen_deps()
    await deps.ledger.seats.set_capacity("src_shared", 5)
    grant = make_grant(clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, seats=())
    await deps.ledger.record_grant(grant)
    request = _wire_request(
        grant.id, kind=WireForageRequestKind.SHARED_SEATS, seats=2, source_id="src_shared"
    )

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.GRANT
    assert outcome.grant is not None
    (reservation,) = outcome.grant.seats
    assert reservation.source_id == "src_shared"
    assert reservation.seats == 2


async def test_shared_seats_grows_an_existing_reservation_on_the_same_source() -> None:
    deps, link, _warden_end = make_queen_deps()
    await deps.ledger.seats.set_capacity("src_shared", 10)
    grant = make_grant(
        clock=deps.clock,
        holder=link.warden_id,
        state=GrantState.ACTIVE,
        seats=(SeatReservation(source_id="src_shared", seats=1),),
    )
    await deps.ledger.record_grant(grant)
    request = _wire_request(
        grant.id, kind=WireForageRequestKind.SHARED_SEATS, seats=2, source_id="src_shared"
    )

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.GRANT
    assert outcome.grant is not None
    (reservation,) = outcome.grant.seats
    assert reservation.seats == 3  # 1 already held + 2 wanted.


async def test_shared_seats_denies_when_no_headroom_and_nothing_to_shrink() -> None:
    deps, link, _warden_end = make_queen_deps()
    await deps.ledger.seats.set_capacity("src_shared", 1)
    grant = make_grant(clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, seats=())
    await deps.ledger.record_grant(grant)
    request = _wire_request(
        grant.id, kind=WireForageRequestKind.SHARED_SEATS, seats=5, source_id="src_shared"
    )

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.DENY


# ──────────────────────────────────────────────────────────────────────────────
# SPEND (roadmap step 4.8)
# ──────────────────────────────────────────────────────────────────────────────


async def test_spend_denies_a_request_with_no_task_id() -> None:
    deps, link, _warden_end = make_queen_deps()
    grant = make_grant(clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE)
    await deps.ledger.record_grant(grant)
    request = _wire_request(grant.id, kind=WireForageRequestKind.SPEND, spend=1.0)

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.DENY
    assert "task_id" in outcome.reason


async def test_spend_grants_within_the_goals_remaining_cap() -> None:
    deps, link, _warden_end = make_queen_deps()  # budgets.spend_cap_usd defaults to 5.0.
    grant = make_grant(
        clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE, spend_budget=0.0
    )
    await deps.ledger.record_grant(grant)
    request = _wire_request(
        grant.id, kind=WireForageRequestKind.SPEND, spend=2.0, task_id=new_task_id(deps.clock)
    )

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.GRANT
    assert outcome.grant is not None
    assert outcome.grant.spend_budget == 2.0


async def test_spend_denies_a_request_beyond_the_goals_cap_never_needs_judgement() -> None:
    deps, link, _warden_end = make_queen_deps()  # budgets.spend_cap_usd defaults to 5.0.
    grant = make_grant(clock=deps.clock, holder=link.warden_id, state=GrantState.ACTIVE)
    await deps.ledger.record_grant(grant)
    request = _wire_request(
        grant.id, kind=WireForageRequestKind.SPEND, spend=10.0, task_id=new_task_id(deps.clock)
    )

    outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)

    # Never NEEDS_JUDGEMENT: a goal's own spend cap is not a shared pool another grant could
    # be shrunk to relieve (hivemind.queen.forage.requests._handle_spend's own docstring).
    assert outcome.autopilot_outcome is ForageAutopilotOutcome.DENY
