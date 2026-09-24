"""Tests for hivemind.workers.roles.guard_bee.respond: report, act, deposit, record the alert.

Every finding becomes a GuardReport, a C2 deposit and one `guard.alert` of ids, enums and counts;
the Guard Bee narrows the whole Hive alone (a raise of a tier's sampled-audit rate, one step over
the rate in force and never past 1.0; an order to the Entrance Reducer), files a request only
through the Queen's door, and falls back to `observe` rather than guess a target.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/respond.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.guard_bee.respond for FindingResponder and build_report.
    - tests.unit.wardens.spawn.test_audited_gate_raised for the raise taking effect in a gate.
"""

from __future__ import annotations

from datetime import timedelta

from builders.guard_bee import GuardBeeRig, RigOptions, make_guard_bee, seed_episode

from hivemind.cell import HoneyClearance
from hivemind.guard import GuardAction
from hivemind.guard.report import MAX_SUMMARY_CHARS
from hivemind.pheromone import FORBIDDEN_PAYLOAD_KEYS
from hivemind.supervision.capping import AuditRateRaise, RiskTier, TierSpec, TierTable, load_tiers
from hivemind.workers.roles.guard_bee import Disposition

_TIER = "SCRATCH_WRITE"  # Shipped at a 0.02 sampled-audit rate.


async def _failing_audits(rig: GuardBeeRig) -> None:
    """Seed one REJECT among four sampled audits of SCRATCH_WRITE: audit_failure_rate fires."""
    episode = await seed_episode(rig.seed)
    for outcome in ("REJECT", "APPROVE", "APPROVE", "APPROVE"):
        proposal = await rig.seed.proposed(episode.task, episode.cell, _TIER)
        await rig.seed.capping(proposal, "capping.audited", tier=_TIER, outcome=outcome)


async def _raises(rig: GuardBeeRig) -> list[AuditRateRaise]:
    events = await rig.kinds("guard.audit_rate_raised")
    return [raised for event in events if (raised := AuditRateRaise.from_event(event))]


async def test_a_request_report_names_its_evidence_and_targets_and_no_content() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    flag = await rig.seed.injection(episode.bee, episode.task)
    denial = await rig.seed.denied(episode.bee)

    [response] = await rig.bee.tick()

    report = response.report
    assert response.disposition is Disposition.FILED and rig.door.filed == [report]
    assert report.event_ids == (flag.id, denial.id)
    assert (report.cell_id, report.bee_ids) == (episode.cell, (episode.bee,))
    assert len(report.summary) <= MAX_SUMMARY_CHARS and episode.bee in report.summary


async def test_every_report_is_one_alert_of_ids_enums_and_counts() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    rig.clock.advance(1.0)  # So the denial alone is the newest counted moment.
    denial = await rig.seed.denied(episode.bee)

    [response] = await rig.bee.tick()

    [alert] = await rig.alerts()
    payload = alert.payload
    assert alert.subject_id == episode.cell
    assert payload["report_id"] == response.report.id
    assert payload["disposition"] == "filed" and payload["action"] == "quarantine_bee"
    assert payload["through"] == denial.at.isoformat() and payload["through_ids"] == [denial.id]
    assert payload["judged"] is False and "rule_action" not in payload
    assert not {key.lower() for key in payload} & FORBIDDEN_PAYLOAD_KEYS


async def test_every_report_is_deposited_at_c2_in_its_cells_folder() -> None:
    rig = make_guard_bee()
    episode = await seed_episode(rig.seed)
    await rig.seed.injection(episode.bee, episode.task)
    await rig.seed.denied(episode.bee)

    await rig.bee.tick()

    [deposit] = rig.sink.deposits
    assert deposit.clearance is HoneyClearance.C2
    assert deposit.scope == f"/cells/{episode.cell}"


async def test_a_failing_tier_is_raised_one_step_over_the_rate_in_force() -> None:
    rig = make_guard_bee()
    await _failing_audits(rig)

    [response] = await rig.bee.tick()
    await _failing_audits(rig)
    await rig.round()

    first, second = await _raises(rig)
    assert response.disposition is Disposition.RAISED
    assert (first.tier, first.from_rate, first.to_rate) == (RiskTier.SCRATCH_WRITE, 0.02, 0.27)
    assert (second.from_rate, second.to_rate) == (0.27, 0.52)  # From the live raise, not 0.02.
    assert first.until == rig.clock.now() - timedelta(seconds=5.0) + timedelta(days=1)
    assert first.report_id == response.report.id


async def test_a_tier_that_samples_everything_is_left_as_it_is() -> None:
    everything = TierSpec(audit_rate=1.0)
    tiers = TierTable(tiers={**load_tiers().tiers, RiskTier.SCRATCH_WRITE: everything})
    rig = make_guard_bee(RigOptions(tiers=tiers))
    await _failing_audits(rig)

    [response] = await rig.bee.tick()

    assert response.disposition is Disposition.AT_CEILING
    assert await _raises(rig) == []


async def test_a_door_burst_orders_the_entrance_reducer_through_the_trail() -> None:
    rig = make_guard_bee()
    for reason in ("request_replay", "request_signature"):
        await rig.seed.entrance("guard.entrance_login_failed", reason=reason, listener="remote")

    [response] = await rig.bee.tick()

    [order] = await rig.kinds("guard.reduce_ordered")
    assert response.disposition is Disposition.REDUCE_ORDERED
    assert response.report.recommended is GuardAction.REDUCE_ENTRANCE
    assert order.subject_id == rig.identity.hive_id
    assert order.payload == {"rule": "request_forgery", "report_id": response.report.id}
    assert rig.door.filed == []  # Narrowing is the Guard Bee's alone: no request.


async def test_a_request_with_no_target_to_name_falls_back_to_observe() -> None:
    rig = make_guard_bee()
    # The grant's spend with no forage.granted join: the finding names a grant, never a bee.
    for _ in range(3):
        await rig.seed.llm_call("grant_01HZZZZZZZZZZZZZZZZZZZZZZZ", 2.0)

    await rig.bee.tick()
    responses = await rig.bee.flush()

    [response] = responses
    assert response.report.recommended is GuardAction.OBSERVE
    assert response.disposition is Disposition.OBSERVED and rig.door.filed == []
