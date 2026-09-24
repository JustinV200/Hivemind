"""Tests for hivemind.supervision.capping.audit's Guard Bee raise: AuditRateRaise and its reader.

Roadmap step 10.6: a Guard Bee raise of one tier's sampled-audit rate travels on the trail as
`guard.audit_rate_raised`; `raised_audit_rate` reads the highest live one back. A raise only ever
raises, an expired or foreign one is ignored, and a malformed row is skipped rather than trusted.

Fits into the Hive:
    Mirrors src/hivemind/supervision/capping/audit.py (codingrules section 3), split by feature
    from test_audit.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.capping.audit for AuditRateRaise and raised_audit_rate.
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
    AuditRateRaise,
    RiskTier,
    raised_audit_rate,
)
from waggle.clock import FakeClock
from waggle.ids import new_event_id

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
