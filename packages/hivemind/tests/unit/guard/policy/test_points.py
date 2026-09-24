"""Tests for hivemind.guard.policy.points: EnforcementPoint.

Fits into the Hive:
    Mirrors src/hivemind/guard/policy/points.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.policy.points for the module under test.
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the point list.
"""

from __future__ import annotations

import re

from hivemind.guard.policy.points import EnforcementPoint


def test_every_point_the_adr_names_is_declared() -> None:
    # ADR-0031's list, in its own order; step 10.3 wires each one to its call site.
    assert [point.value for point in EnforcementPoint] == [
        "placement",
        "lease_creation",
        "grant_issue",
        "forage_request",
        "warden_spawn",
        "tool_invocation",
        "session_outside_scratch",
        "exoskeleton_real_display",
        "honey_access",
        "slot_binding",
        "question_routing",
        "nuc_promotion",
        "device_command",
        "tactic_invocation",
        "comb_shield_egress",
        "entrance_route",
        "isolation",
        "quarantine",
        "taint_clear",
        "sting_cut",
        "supersedure",
        "absconding",
    ]


def test_point_values_are_lowercase_snake_names_of_their_members() -> None:
    # A manifest's [guard.escalation] keys and every guard.denied event name points by value.
    for point in EnforcementPoint:
        assert re.fullmatch(r"[a-z]+(_[a-z]+)*", point.value)
        assert point.value == point.name.lower()
