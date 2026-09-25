"""Tests for hivemind.supervision.capping.audit.raises: a Guard Bee raise, and every way it is read.

Roadmap step 10.6: a Guard Bee raise of one tier's sampled-audit rate travels on the trail as
`guard.audit_rate_raised`; `raised_audit_rate` reads the highest live one back, and
`live_audit_raises` the highest per tier, for a grant to carry. A raise only ever raises, an
expired or foreign one is ignored, and a malformed row is skipped rather than trusted. A Warden's
`CarriedAuditRaises` keeps what its grants carried, per tier the highest still in force, and
ignores a tier it does not know.

Fits into the Hive:
    Mirrors src/hivemind/supervision/capping/audit/raises.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.capping.audit.raises for the module under test.
    - tests.unit.wardens.spawn.test_audited_gate_raised for a raise taking effect in a gate.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.cells import make_identity
from pydantic import JsonValue, ValidationError

from hivemind.guard import new_guard_report_id
from hivemind.pheromone import GuardEvent, MemoryPheromoneTrail
from hivemind.supervision.capping import (
    AUDIT_RATE_RAISED_KIND,
    MAX_CARRIED_PER_TIER,
    AuditRateRaise,
    CarriedAuditRaises,
    RiskTier,
    live_audit_raises,
    raised_audit_rate,
)
from waggle.clock import FakeClock
from waggle.ids import new_event_id
from waggle.messages.forage import RaisedAuditRate

_HOLD = timedelta(hours=1)  # How long every raise in this module lasts.


def _raise(clock: FakeClock, tier: RiskTier, to_rate: float, **overrides: object) -> AuditRateRaise:
    fields: dict[str, object] = {
        "tier": tier,
        "from_rate": 0.0,
        "to_rate": to_rate,
        "until": clock.now() + _HOLD,
        "report_id": new_guard_report_id(clock),
        "rule": "audit_failure_rate",
    }
    fields.update(overrides)
    return AuditRateRaise.model_validate(fields)


async def _record(
    trail: MemoryPheromoneTrail, clock: FakeClock, payload: dict[str, JsonValue]
) -> None:
    identity = make_identity(clock)
    await trail.record(
        GuardEvent(
            id=new_event_id(clock),
            hive_id=identity.hive_id,
            node_id=identity.node_id,
            at=clock.now(),
            actor=identity.actor,
            kind=AUDIT_RATE_RAISED_KIND,
            subject_id=identity.hive_id,
            payload=payload,
        )
    )


def test_a_raise_that_does_not_raise_is_refused() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="must raise"):
        _raise(clock, RiskTier.SCRATCH_WRITE, 0.1, from_rate=0.2)


def test_a_raise_round_trips_through_its_trail_payload() -> None:
    clock = FakeClock()
    original = _raise(clock, RiskTier.NETWORK_EGRESS, 0.35, from_rate=0.1)

    payload = original.to_payload()

    assert AuditRateRaise.model_validate(payload) == original
    assert set(payload) == {"tier", "from_rate", "to_rate", "until", "report_id", "rule"}


async def test_the_highest_live_raise_for_the_tier_is_the_one_read_back() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    await _record(trail, clock, _raise(clock, RiskTier.SCRATCH_WRITE, 0.27).to_payload())
    clock.advance(1.0)
    await _record(trail, clock, _raise(clock, RiskTier.SCRATCH_WRITE, 0.52).to_payload())
    await _record(trail, clock, _raise(clock, RiskTier.NETWORK_EGRESS, 0.9).to_payload())

    rate = await raised_audit_rate(trail, RiskTier.SCRATCH_WRITE, clock.now())

    assert rate == 0.52


async def test_an_expired_raise_and_another_tiers_raise_are_ignored() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    await _record(trail, clock, _raise(clock, RiskTier.SCRATCH_WRITE, 0.5).to_payload())
    await _record(trail, clock, _raise(clock, RiskTier.NETWORK_EGRESS, 0.9).to_payload())
    clock.advance(_HOLD.total_seconds() + 1.0)

    rate = await raised_audit_rate(trail, RiskTier.SCRATCH_WRITE, clock.now())

    assert rate == 0.0


async def test_a_malformed_raise_row_is_skipped_rather_than_trusted() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    # A row that claims a lowering: the model refuses it, so the reader never acts on it.
    lowering = _raise(clock, RiskTier.SCRATCH_WRITE, 0.4).to_payload()
    lowering["from_rate"] = 0.9
    await _record(trail, clock, lowering)
    await _record(trail, clock, {"tier": "SCRATCH_WRITE"})

    rate = await raised_audit_rate(trail, RiskTier.SCRATCH_WRITE, clock.now())

    assert rate == 0.0


async def test_the_raises_a_grant_carries_are_the_highest_per_tier_still_in_force() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    await _record(trail, clock, _raise(clock, RiskTier.SCRATCH_WRITE, 0.27).to_payload())
    await _record(trail, clock, _raise(clock, RiskTier.SCRATCH_WRITE, 0.52).to_payload())
    expired = _raise(clock, RiskTier.NETWORK_EGRESS, 0.9, until=clock.now())
    await _record(trail, clock, expired.to_payload())
    await _record(trail, clock, {"tier": "SCRATCH_WRITE"})  # Malformed: never carried.
    clock.advance(1.0)

    carried = await live_audit_raises(trail, clock.now())

    assert [(r.tier, r.to_rate) for r in carried] == [(RiskTier.SCRATCH_WRITE, 0.52)]


def _carried(tier: str, rate: float, until_s: float, clock: FakeClock) -> RaisedAuditRate:
    return RaisedAuditRate(tier=tier, rate=rate, until=clock.now() + timedelta(seconds=until_s))


def test_a_warden_keeps_the_highest_carried_raise_in_force_per_tier() -> None:
    clock = FakeClock()
    held = CarriedAuditRaises()

    held.carry([_carried("SCRATCH_WRITE", 0.27, 60.0, clock)], clock.now())
    held.carry([_carried("SCRATCH_WRITE", 0.52, 30.0, clock)], clock.now())
    before = held.rate(RiskTier.SCRATCH_WRITE, clock.now())
    clock.advance(45.0)  # The higher raise has lapsed; the lower one still holds.
    after = held.rate(RiskTier.SCRATCH_WRITE, clock.now())
    clock.advance(30.0)

    assert (before, after) == (0.52, 0.27)
    assert held.rate(RiskTier.SCRATCH_WRITE, clock.now()) == 0.0
    assert held.rate(RiskTier.NETWORK_EGRESS, clock.now()) == 0.0


def test_a_carried_raise_of_an_unknown_tier_or_already_past_is_ignored() -> None:
    clock = FakeClock()
    held = CarriedAuditRaises()

    held.carry(
        [
            _carried("A_TIER_FROM_A_LATER_MINOR", 1.0, 60.0, clock),
            _carried("SCRATCH_WRITE", 0.5, -1.0, clock),
        ],
        clock.now(),
    )

    assert all(held.rate(tier, clock.now()) == 0.0 for tier in RiskTier)


def test_a_warden_keeps_a_bounded_number_of_carried_raises_per_tier() -> None:
    clock = FakeClock()
    held = CarriedAuditRaises()

    for step in range(MAX_CARRIED_PER_TIER * 2):
        held.carry([_carried("SCRATCH_WRITE", 0.01 * (step + 1), 60.0, clock)], clock.now())

    assert held.rate(RiskTier.SCRATCH_WRITE, clock.now()) == 0.01 * (MAX_CARRIED_PER_TIER * 2)
