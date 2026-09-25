"""Tests for the Guard Bee showing a CRITICAL report to the human through the Queen's door.

ADR-0043: a report at CRITICAL confidence reaches the human whatever it recommends. The Guard Bee
shows one that asks for nothing (a raised audit rate, an Entrance reduce order, an observation)
through `GuardRequestDoor.report_to_human`, after carrying out what it recommends, and records
that it did on the alert. A CRITICAL request is filed and never shown here, since the Queen shows
it herself, once, with her decision on it; anything below CRITICAL is only ever an alert.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/guard_bee/respond.py (codingrules section 3), split by
    feature from test_respond.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.guard_requests.door for the Queen's side of `report_to_human`.
"""

from __future__ import annotations

from builders.guard_bee import SHIPPED_RULE_SEEDERS, GuardBeeRig, RigOptions, make_guard_bee

from hivemind.guard import GuardAction
from hivemind.manifest.schema.guard import GuardBeeRuleOverride, GuardBeeSection, GuardSection
from hivemind.workers.roles.guard_bee import Disposition, Response


def _critical(*overrides: tuple[str, GuardBeeRuleOverride]) -> GuardBeeRig:
    """A Guard Bee whose named rules are overridden, each to CRITICAL unless said otherwise."""
    rules = dict(overrides)
    return make_guard_bee(RigOptions(guard=GuardSection(bee=GuardBeeSection(rules=rules))))


async def _fire(rig: GuardBeeRig, key: str) -> Response:
    """Seed the shipped rule `key`'s trail, run one round, and return its one response."""
    await SHIPPED_RULE_SEEDERS[key](rig, True)
    [response] = await rig.bee.tick()
    return response


async def test_a_critical_raise_is_carried_out_and_shown_to_the_human() -> None:
    rig = _critical(("audit_failure_rate", GuardBeeRuleOverride(confidence="critical")))

    response = await _fire(rig, "audit_failure_rate")

    assert response.disposition is Disposition.RAISED
    assert rig.door.shown == [response.report] and rig.door.filed == []
    [alert] = await rig.alerts()
    assert alert.payload["shown"] is True


async def test_a_critical_reduce_order_is_given_and_shown_to_the_human() -> None:
    rig = _critical(("request_forgery", GuardBeeRuleOverride(confidence="critical")))

    response = await _fire(rig, "request_forgery")

    assert response.disposition is Disposition.REDUCE_ORDERED
    assert await rig.kinds("guard.reduce_ordered")
    assert rig.door.shown == [response.report]


async def test_a_critical_observation_is_shown_to_the_human() -> None:
    rig = _critical(("travel_lock_triggered", GuardBeeRuleOverride(confidence="critical")))

    response = await _fire(rig, "travel_lock_triggered")

    assert response.report.recommended is GuardAction.OBSERVE
    assert rig.door.shown == [response.report]


async def test_a_critical_request_is_filed_and_never_shown_here_too() -> None:
    rig = _critical(("injection_then_denial", GuardBeeRuleOverride(confidence="critical")))

    response = await _fire(rig, "injection_then_denial")

    assert response.disposition is Disposition.FILED
    assert rig.door.filed == [response.report] and rig.door.shown == []
    [alert] = await rig.alerts()
    assert alert.payload["shown"] is False


async def test_anything_below_critical_is_never_shown() -> None:
    rig = make_guard_bee()

    response = await _fire(rig, "request_forgery")  # Shipped at high.

    assert response.disposition is Disposition.REDUCE_ORDERED
    assert rig.door.shown == []
