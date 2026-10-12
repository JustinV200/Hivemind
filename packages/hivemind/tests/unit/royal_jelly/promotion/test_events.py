"""Tests for hivemind.royal_jelly.promotion.events: the `tool.*` events each stage writes.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/promotion/events.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.promotion.events for the module under test.
"""

from __future__ import annotations

import pytest
from builders.royal_jelly import make_brief, make_offer, make_scaffold_result, make_tool_spec
from pydantic import ValidationError

from hivemind.pheromone.events.base import MAX_PAYLOAD_STRING_CHARS
from hivemind.royal_jelly.promotion import (
    SHA256_PREFIX_CHARS,
    ToolIdentity,
    load_refused_event,
    loaded_event,
    promoted_event,
    quarantine_requested_event,
    quarantined_event,
    rejected_event,
    requested_event,
    scaffolded_event,
    tool_event,
)
from hivemind.royal_jelly.registry import InMemoryCombRegistry, QueenPrincipal
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id, new_tool_id


def _identity(clock: FakeClock) -> ToolIdentity:
    return ToolIdentity(new_hive_id(clock), new_node_id(clock), "system")


def test_tool_event_stamps_identity_time_and_subject() -> None:
    clock = FakeClock()
    identity = _identity(clock)
    tool_id = new_tool_id(clock)

    event = tool_event(identity, clock, "tool.loaded", tool_id, name="echo_tool", attempt=2)

    assert (event.hive_id, event.node_id, event.actor) == (
        identity.hive_id,
        identity.node_id,
        "system",
    )
    assert event.subject_id == tool_id and event.at == clock.now()
    assert event.payload == {"name": "echo_tool", "attempt": 2}


def test_tool_event_cuts_an_overlong_reason_to_the_trail_bound() -> None:
    clock = FakeClock()

    event = tool_event(
        _identity(clock), clock, "tool.rejected", new_tool_id(clock), reason="r" * 50_000
    )

    assert len(str(event.payload["reason"])) < MAX_PAYLOAD_STRING_CHARS


def test_tool_event_refuses_a_kind_outside_the_tool_family() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        tool_event(_identity(clock), clock, "honey.ripened", new_tool_id(clock))


def test_requested_event_carries_the_request_facts_and_no_description() -> None:
    clock = FakeClock()
    brief = make_brief(clock)

    event = requested_event(_identity(clock), clock, brief)

    assert event.kind == "tool.requested" and event.subject_id == brief.tool_id
    assert event.payload["name"] == "echo_tool" and event.payload["os"] == "LINUX"
    assert brief.description not in str(event.payload)


def test_scaffolded_event_names_the_package_by_hash_prefix_and_size() -> None:
    clock = FakeClock()
    result = make_scaffold_result(make_brief(clock))

    event = scaffolded_event(
        _identity(clock),
        clock,
        result.spec,
        (result.package_sha256, result.package_size_bytes, 2),
    )

    assert event.payload["package_sha256"] == result.package_sha256[:SHA256_PREFIX_CHARS]
    assert event.payload["size_bytes"] == result.package_size_bytes
    assert event.payload["attempt"] == 2


def test_quarantine_events_name_the_stage_the_verdict_and_the_failing_gate() -> None:
    clock = FakeClock()
    spec = make_tool_spec(clock)
    report, _offer = make_offer(spec)
    identity = _identity(clock)

    requested = quarantine_requested_event(identity, clock, spec, 1)
    passed = quarantined_event(identity, clock, report)

    assert requested.kind == "tool.quarantine_requested" and requested.payload["attempt"] == 1
    assert passed.kind == "tool.quarantined"
    assert passed.payload["passed"] is True and passed.payload["failing_gate"] is None
    assert passed.payload["path"] == "SANDBOX_CELL"


async def test_promoted_event_reads_the_registry_change() -> None:
    clock = FakeClock()
    spec = make_tool_spec(clock)
    report, offer = make_offer(spec)
    change = await InMemoryCombRegistry(clock).promote(
        spec, report, offer, QueenPrincipal(), "because"
    )

    event = promoted_event(_identity(clock), clock, change)

    assert event.kind == "tool.promoted" and event.subject_id == spec.tool_id
    assert event.payload["version"] == "1.0.0" and event.payload["promoted_by"] == "queen"
    assert event.payload["supersedes_version"] is None


def test_rejected_loaded_and_load_refused_events() -> None:
    clock = FakeClock()
    identity, tool_id = _identity(clock), new_tool_id(clock)

    rejected = rejected_event(identity, clock, tool_id, ("quarantine", "tests", "one failed"))
    loaded = loaded_event(identity, clock, tool_id, ("echo_tool", "1.0.0", "a" * 64))
    refused = load_refused_event(identity, clock, tool_id, ("echo_tool", "hash_mismatch", "bad"))

    assert rejected.payload == {"stage": "quarantine", "code": "tests", "reason": "one failed"}
    assert loaded.payload["package_sha256"] == "a" * SHA256_PREFIX_CHARS
    assert (refused.kind, refused.payload["code"]) == ("tool.load_refused", "hash_mismatch")
