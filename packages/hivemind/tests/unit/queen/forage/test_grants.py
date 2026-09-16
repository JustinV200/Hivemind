"""Tests for hivemind.queen.forage.grants: activate, revise, renew, revoke, sweep_expired.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/grants.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.grants for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_capacity, make_grant
from builders.queen import make_queen_deps

from hivemind.forage.errors import InvalidGrantTransitionError
from hivemind.forage.grant_state import GrantState
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.forage.grants import (
    activate,
    renew_grants_for_warden,
    revise,
    revoke,
    sweep_expired,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.forage.values import RevocationCause


def test_activate_moves_issued_to_active() -> None:
    grant = make_grant(state=GrantState.ISSUED)

    result = activate(grant)

    assert result.state is GrantState.ACTIVE
    assert result.id == grant.id  # Same grant, only the state changed.


def test_activate_rejects_a_grant_not_at_issued() -> None:
    grant = make_grant(state=GrantState.ACTIVE)

    with pytest.raises(InvalidGrantTransitionError):
        activate(grant)


async def test_revise_upserts_the_ledgers_own_copy() -> None:
    deps, _link, _warden_end = make_queen_deps()
    grant = make_grant(clock=deps.clock, state=GrantState.ACTIVE, max_sub_bees=2)
    await deps.ledger.record_grant(grant)
    grown = grant.model_copy(update={"max_sub_bees": 5, "revision": 1})

    await revise(deps.ledger, grown)

    stored = deps.ledger.grant(grant.id)
    assert stored is not None
    assert stored.max_sub_bees == 5
    assert stored.revision == 1


async def test_renew_grants_for_warden_extends_only_that_holders_own_grants() -> None:
    clock = FakeClock()
    deps, link, _warden_end = make_queen_deps(clock)
    mine = make_grant(clock=clock, holder=link.warden_id, state=GrantState.ACTIVE)
    other = make_grant(clock=clock, state=GrantState.ACTIVE)  # A different holder.
    await deps.ledger.record_grant(mine)
    await deps.ledger.record_grant(other)
    original_expiry = mine.expires_at
    clock.advance(100.0)

    renewed = await renew_grants_for_warden(deps.ledger, deps, link.warden_id, ttl_s=300.0)

    assert len(renewed) == 1
    assert renewed[0].id == mine.id
    assert renewed[0].expires_at > original_expiry
    stored_other = deps.ledger.grant(other.id)
    assert stored_other is not None
    assert stored_other.expires_at == other.expires_at  # Untouched: a different holder.


async def test_renew_grants_for_warden_is_a_no_op_for_a_holder_with_no_live_grants() -> None:
    deps, link, _warden_end = make_queen_deps()

    renewed = await renew_grants_for_warden(deps.ledger, deps, link.warden_id, ttl_s=300.0)

    assert renewed == ()


async def test_revoke_moves_active_to_revoked_frees_headroom_and_records_the_trail() -> None:
    deps, _link, _warden_end = make_queen_deps()
    await deps.ledger.report_capacity(new_cell_id(deps.clock), make_capacity(max_sub_bees=10))
    grant = make_grant(clock=deps.clock, state=GrantState.ACTIVE, max_sub_bees=3)
    await deps.ledger.record_grant(grant)
    headroom_before = deps.ledger.headroom().sub_bees

    revoked = await revoke(
        deps.ledger, deps, grant, RevocationCause.RECLAIMED, "the Queen took it back"
    )

    assert revoked.state is GrantState.REVOKED
    assert deps.ledger.grant(grant.id) is None
    assert deps.ledger.headroom().sub_bees == headroom_before + 3
    events = await deps.trail.query(TrailQuery())
    assert [e.kind for e in events] == ["forage.revoked"]
    assert events[0].subject_id == grant.id
    assert events[0].payload["cause"] == RevocationCause.RECLAIMED.value


async def test_revoke_rejects_a_grant_still_at_issued() -> None:
    deps, _link, _warden_end = make_queen_deps()
    grant = make_grant(clock=deps.clock, state=GrantState.ISSUED)

    with pytest.raises(InvalidGrantTransitionError):
        await revoke(deps.ledger, deps, grant, RevocationCause.EXPIRED, "never activated")


async def test_sweep_expired_revokes_only_grants_past_their_own_expiry() -> None:
    clock = FakeClock()
    deps, _link, _warden_end = make_queen_deps(clock)
    expired = make_grant(clock=clock, state=GrantState.ACTIVE, expires_at=clock.now())
    fresh = make_grant(clock=clock, state=GrantState.ACTIVE)
    await deps.ledger.record_grant(expired)
    await deps.ledger.record_grant(fresh)
    clock.advance(1.0)  # Past expired's own deadline; fresh's own is far in the future.

    revoked = await sweep_expired(deps.ledger, deps)

    assert [g.id for g in revoked] == [expired.id]
    assert deps.ledger.grant(expired.id) is None
    assert deps.ledger.grant(fresh.id) is not None
    events = await deps.trail.query(TrailQuery())
    assert [e.kind for e in events] == ["forage.revoked"]
    assert events[0].payload["cause"] == RevocationCause.EXPIRED.value


async def test_sweep_expired_is_a_no_op_with_nothing_expired() -> None:
    deps, _link, _warden_end = make_queen_deps()
    fresh = make_grant(clock=deps.clock, state=GrantState.ACTIVE)
    await deps.ledger.record_grant(fresh)

    revoked = await sweep_expired(deps.ledger, deps)

    assert revoked == ()
    assert deps.ledger.grant(fresh.id) is not None
