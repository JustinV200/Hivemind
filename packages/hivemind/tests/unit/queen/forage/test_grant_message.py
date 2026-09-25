"""Tests for hivemind.queen.forage.grants.grant_message: every grant carries the raises in force.

Roadmap step 10.6 (Waggle 1.10): a Guard Bee raise of a Capping tier's audit rate lives on the
Queen's trail, which a Virtual Cell's in-Cell Warden never sees, so every `GrantIssued` she sends
carries the raises in force at that moment, the highest per tier and none that has lapsed.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/grants.py (codingrules section 3), split by feature from
    test_grants.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.wardens.ticks.test_assign_raises for the Warden keeping what a grant carried.
    - tests.unit.wardens.spawn.test_audited_gate_raised for a carried raise taking effect.
    - tests.e2e.test_audit_raise_on_virtual_cell for the same over a real in-Cell Warden.
"""

from __future__ import annotations

from datetime import timedelta

from builders.forage import make_grant
from builders.queen import make_queen_deps, plan_responder

from hivemind.cell import HoneyClearance
from hivemind.guard import new_guard_report_id
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import GuardEvent, PheromoneTrail
from hivemind.queen.deps import QueenDeps
from hivemind.queen.forage.grants import grant_message
from hivemind.queen.queen import Queen
from hivemind.supervision.capping import AUDIT_RATE_RAISED_KIND, AuditRateRaise, RiskTier
from waggle.clock import FakeClock
from waggle.ids import new_event_id

_HOLD = timedelta(hours=1)  # How long every raise here lasts.


async def _raise(deps: QueenDeps, tier: RiskTier, to_rate: float, hold: timedelta = _HOLD) -> None:
    """Record the Guard Bee's raise of `tier` to `to_rate` on the Queen's trail, as it does."""
    clock = deps.clock
    raised = AuditRateRaise(
        tier=tier,
        from_rate=0.0,
        to_rate=to_rate,
        until=clock.now() + hold,
        report_id=new_guard_report_id(clock),
        rule="audit_failure_rate",
    )
    await _record(deps.trail, deps, raised)


async def _record(trail: PheromoneTrail, deps: QueenDeps, raised: AuditRateRaise) -> None:
    identity = deps.identity
    await trail.record(
        GuardEvent(
            id=new_event_id(deps.clock),
            hive_id=identity.hive_id,
            node_id=identity.node_id,
            at=deps.clock.now(),
            actor="system",
            kind=AUDIT_RATE_RAISED_KIND,
            subject_id=identity.hive_id,
            payload=raised.to_payload(),
        )
    )


def _plan(goal: str) -> dict[str, object]:
    acceptance: dict[str, object] = {"kind": "FILE_EXISTS", "subject": "done.txt"}
    acceptance |= {"argv": [], "expected": None}
    task = {
        "key": "root",
        "title": "Root task",
        "objective": f"Do the work for: {goal}",
        "acceptance": [acceptance],
        "needs": {},
        "clearance": "C1",
        "depends_on": [],
    }
    return {"tasks": [task]}


async def test_a_grant_carries_the_highest_raise_in_force_per_tier_and_none_lapsed() -> None:
    deps, _, _ = make_queen_deps()
    await _raise(deps, RiskTier.SCRATCH_WRITE, 0.27)
    await _raise(deps, RiskTier.SCRATCH_WRITE, 0.52)
    await _raise(deps, RiskTier.NETWORK_EGRESS, 1.0, hold=timedelta(seconds=1))
    assert isinstance(deps.clock, FakeClock)
    deps.clock.advance(2.0)  # The network raise has lapsed.

    message = await grant_message(deps, make_grant(clock=deps.clock))

    assert [(r.tier, r.rate) for r in message.audit_raises] == [("SCRATCH_WRITE", 0.52)]


async def test_the_grant_a_dispatch_sends_carries_the_raises_to_its_warden() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await _raise(deps, RiskTier.SCRATCH_WRITE, 1.0)

    await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    grant = await warden_end.wait_for_grant()
    await warden_end.close()

    [carried] = grant.audit_raises
    assert (carried.tier, carried.rate) == ("SCRATCH_WRITE", 1.0)
