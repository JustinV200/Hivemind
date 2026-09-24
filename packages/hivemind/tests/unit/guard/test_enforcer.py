"""Tests for hivemind.guard.enforcer: Enforcer records every refusal on the trail, never an allow.

Fits into the Hive:
    Mirrors src/hivemind/guard/enforcer.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.enforcer for the module under test.
    - hivemind.pheromone.events.families for the guard.denied kind.
"""

from __future__ import annotations

import pytest

from hivemind.cell import CellIdentity
from hivemind.guard.capabilities import Capability, CapabilitySet
from hivemind.guard.enforcer import DENIED_KIND, Enforcer
from hivemind.guard.policy import (
    OPERATOR_ID,
    EscalationAction,
    PolicyContext,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
    load_guard_policy,
)
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.manifest import GuardSection
from hivemind.pheromone import MAX_PAYLOAD_STRING_CHARS, GuardEvent, PheromoneEvent, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import HiveId, IdKind, NodeId, new_id


def _enforcer() -> tuple[Enforcer, MemoryPheromoneTrail, CellIdentity]:
    """Build an Enforcer over the shipped policy with tool invocation escalating to an Alarm."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    identity = CellIdentity(
        hive_id=HiveId(new_id(IdKind.HIVE, clock)),
        node_id=NodeId(new_id(IdKind.NODE, clock)),
        actor="system",
    )
    policy = load_guard_policy(None, GuardSection(escalation={"tool_invocation": "alarm"}))
    return Enforcer(policy, trail, clock, identity), trail, identity


def _worker_request(needed: str, *held: str) -> PolicyRequest:
    """Build a Drone's tool-invocation request for `needed`, holding `held`."""
    worker_id = new_id(IdKind.WORKER, FakeClock())
    return PolicyRequest(
        principal=PrincipalRef(kind=PrincipalKind.WORKER, id=worker_id, role="drone"),
        point=EnforcementPoint.TOOL_INVOCATION,
        needed=Capability.parse(needed),
        held=CapabilitySet.parse(*held),
        context=PolicyContext(),
    )


async def _events(trail: MemoryPheromoneTrail) -> tuple[PheromoneEvent, ...]:
    """Return every event on `trail`."""
    return await trail.query(TrailQuery())


async def test_an_allowed_action_writes_nothing_to_the_trail() -> None:
    enforcer, trail, _identity = _enforcer()

    decision = await enforcer.check(_worker_request("tool:read_file", "tool:*"))

    assert decision.allowed is True
    assert await _events(trail) == ()


async def test_a_refusal_is_returned_not_raised_and_recorded_as_guard_denied() -> None:
    enforcer, trail, identity = _enforcer()
    request = _worker_request("net:api.example.com", "tool:*")

    decision = await enforcer.check(request)

    assert decision.allowed is False
    (event,) = await _events(trail)
    assert isinstance(event, GuardEvent)
    assert event.kind == DENIED_KIND
    assert event.actor == identity.actor
    assert event.subject_id == request.principal.id
    assert event.payload == {
        "principal_kind": "worker",
        "principal_id": request.principal.id,
        "role": "drone",
        "point": "tool_invocation",
        "capability": "net:api.example.com",
        "rule": "guard.not_held",
        "reason": decision.reason,
        "escalation": "alarm",
    }
    assert decision.escalation is EscalationAction.ALARM


async def test_a_refusal_of_the_operator_is_recorded_about_the_hive() -> None:
    enforcer, trail, identity = _enforcer()
    request = PolicyRequest(
        principal=PrincipalRef(kind=PrincipalKind.OPERATOR, id=OPERATOR_ID, role="operator"),
        point=EnforcementPoint.SUPERSEDURE,
        needed=Capability.parse("supersede"),
        held=CapabilitySet.empty(),
    )

    await enforcer.check(request)

    (event,) = await _events(trail)
    assert event.subject_id == identity.hive_id
    assert event.payload["principal_id"] == "human"


async def test_a_long_capability_and_reason_are_shortened_to_fit_the_trail() -> None:
    enforcer, trail, _identity = _enforcer()
    long_path = "/" + "d" * (2 * MAX_PAYLOAD_STRING_CHARS)

    await enforcer.check(_worker_request(f"fs:write:{long_path}"))

    (event,) = await _events(trail)
    for key in ("capability", "reason"):
        value = event.payload[key]
        assert isinstance(value, str)
        assert len(value) == MAX_PAYLOAD_STRING_CHARS
        assert value.endswith("…")


async def test_a_trail_failure_propagates_so_no_refusal_goes_unrecorded() -> None:
    enforcer, trail, _identity = _enforcer()

    async def failing_record(event: PheromoneEvent) -> None:
        raise OSError("disk full")

    trail.record = failing_record  # type: ignore[method-assign]  # The failure is the test.

    with pytest.raises(OSError, match="disk full"):
        await enforcer.check(_worker_request("net:api.example.com"))


def test_the_enforcer_exposes_its_policy() -> None:
    enforcer, _trail, _identity = _enforcer()

    assert enforcer.policy.escalation_for(EnforcementPoint.TOOL_INVOCATION) is (
        EscalationAction.ALARM
    )
