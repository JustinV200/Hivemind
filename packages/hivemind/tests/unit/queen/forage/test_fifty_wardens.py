"""Synthetic test: fifty Wardens contend for a small shared Forage capacity.

Roadmap step 4.7: "A synthetic test starts fifty Wardens against a small fake capacity and
asserts the sum of grants never exceeds capacity minus reserve." Each Warden here is a "fake
link" in the sense the roadmap allows: a `WardenId` and a standing `ForageGrant` at zero
sub-bees, with no real Waggle transport or Queen tick loop -- `hivemind.queen.forage.requests.
handle_forage_request_for_kind` is the same production code path `hivemind.queen.ticks.forage`
calls, so this exercises the real allocator-plus-ledger machinery, not a re-implementation of it.

Fits into the Hive:
    Integration-style unit test for hivemind.queen.forage.ledger and .requests together
    (codingrules section 3: not a one-to-one mirror of a single source module, the same way
    tests/unit/queen/test_queen_invariants.py is a cross-cutting check rather than one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 4.7 for the exact synthetic-test requirement this satisfies.
    - hivemind.queen.forage.requests and .ledger for the modules under test.
"""

from __future__ import annotations

from builders.forage import make_capacity, make_grant, make_reserve
from builders.queen import make_queen_deps

from hivemind.forage.grant_state import GrantState
from hivemind.queen.autopilot import ForageAutopilotOutcome
from hivemind.queen.forage.requests import handle_forage_request_for_kind
from waggle.ids import new_cell_id, new_warden_id
from waggle.messages.forage import ForageDelta
from waggle.messages.forage import ForageRequest as WireForageRequest
from waggle.messages.forage.values import ForageRequestKind as WireForageRequestKind
from waggle.messages.labels import AccuracyBar as WireAccuracyBar
from waggle.messages.labels import Tempo as WireTempo

_WARDEN_COUNT = 50
_SHARED_CAPACITY = 40  # Far fewer sub-bee slots than fifty Wardens asking for two each will want.
_ASK_PER_WARDEN = 2


def _request(grant_id: str, sub_bees: int) -> WireForageRequest:
    delta = ForageDelta(
        seats=0,
        source_id=None,
        spend=0.0,
        tokens=0,
        sub_bees=sub_bees,
        slot=None,
        minimum_grade=None,
    )
    return WireForageRequest(
        grant_id=grant_id,
        kind=WireForageRequestKind.SUB_BEES,
        wanted=delta,
        task_id=None,
        tempo=WireTempo(latency_budget_s=None, accuracy=WireAccuracyBar.NORMAL),
        reason="synthetic fifty-Warden contention test",
    )


async def test_fifty_wardens_never_collectively_exceed_capacity_minus_reserve() -> None:
    deps, _link, _warden_end = make_queen_deps()
    reserve = make_reserve(seats=1, headroom_fraction=0.1)
    await deps.ledger.set_reserve(reserve)
    await deps.ledger.report_capacity(
        new_cell_id(deps.clock), make_capacity(max_sub_bees=_SHARED_CAPACITY)
    )
    cap = int(_SHARED_CAPACITY * (1 - reserve.headroom_fraction)) - reserve.seats

    # Fifty fake Wardens, each starting from a standing grant of zero sub-bees.
    grants = []
    for _ in range(_WARDEN_COUNT):
        holder = new_warden_id(deps.clock)
        grant = make_grant(clock=deps.clock, holder=holder, state=GrantState.ACTIVE, max_sub_bees=0)
        await deps.ledger.record_grant(grant)
        grants.append(grant)

    outcomes = set()
    for grant in grants:
        request = _request(grant.id, sub_bees=_ASK_PER_WARDEN)
        outcome = await handle_forage_request_for_kind(deps.ledger, deps, request)
        outcomes.add(outcome.autopilot_outcome)
        # Checked after every single request, not only at the end: the invariant must hold at
        # every moment, never only in the final tally.
        total_live = sum(g.max_sub_bees for g in deps.ledger.live_grants())
        assert total_live <= cap

    # Demand (50 * 2 = 100) is well over the cap, so the pool must have run out for some Wardens.
    assert ForageAutopilotOutcome.GRANT in outcomes
    assert outcomes & {ForageAutopilotOutcome.DENY, ForageAutopilotOutcome.NEEDS_JUDGEMENT}
    total_live = sum(g.max_sub_bees for g in deps.ledger.live_grants())
    assert total_live <= cap
    assert deps.ledger.headroom().sub_bees == cap - total_live
