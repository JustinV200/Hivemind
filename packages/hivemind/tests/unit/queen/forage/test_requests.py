"""Tests for hivemind.queen.forage.requests: handle_sub_bee_request.

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
from hivemind.queen.autopilot import ForageAutopilotOutcome
from hivemind.queen.forage.requests import handle_sub_bee_request
from waggle.ids import new_cell_id, new_grant_id
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


async def test_denies_a_non_sub_bees_kind_with_the_scope_reason() -> None:
    deps, _link, _warden_end = make_queen_deps()
    request = _wire_request(new_grant_id(deps.clock), kind=WireForageRequestKind.SPEND)

    outcome = await handle_sub_bee_request(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.DENY
    assert outcome.grant is None
    assert "SUB_BEES" in outcome.reason


async def test_denies_a_grant_id_the_ledger_does_not_know() -> None:
    deps, _link, _warden_end = make_queen_deps()
    request = _wire_request(new_grant_id(deps.clock))

    outcome = await handle_sub_bee_request(deps.ledger, deps, request)

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

    outcome = await handle_sub_bee_request(deps.ledger, deps, request)

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

    outcome = await handle_sub_bee_request(deps.ledger, deps, request)

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

    outcome = await handle_sub_bee_request(deps.ledger, deps, request)

    assert outcome.autopilot_outcome is ForageAutopilotOutcome.NEEDS_JUDGEMENT
    assert outcome.grant is None
    stored = deps.ledger.grant(mine.id)
    assert stored is not None
    assert stored.max_sub_bees == 2  # Never mutated on NEEDS_JUDGEMENT.
