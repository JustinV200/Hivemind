"""Tests for hivemind.entrance.enrol.deps.identity: the one place guard.entrance_* events are built.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/deps/identity.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.deps.identity for the module under test.
"""

from __future__ import annotations

import pytest
from builders.entrance import make_identity
from pydantic import ValidationError

from hivemind.pheromone import GuardEvent
from waggle.clock import FakeClock

_DEVICE = "device_01M221E4C10R4XDPNQNRX85AAA"


def test_an_event_carries_the_identitys_hive_node_and_default_actor() -> None:
    clock = FakeClock()
    identity = make_identity(clock)

    event = identity.event(clock, "guard.entrance_expired", _DEVICE, {"expired_from": "PENDING"})

    assert isinstance(event, GuardEvent)
    assert (event.hive_id, event.node_id, event.actor) == (
        identity.hive_id,
        identity.node_id,
        "system",
    )
    assert (event.at, event.subject_id) == (clock.now(), _DEVICE)
    assert event.payload == {"expired_from": "PENDING"}


def test_an_event_may_name_its_own_actor_and_each_gets_a_fresh_id() -> None:
    clock = FakeClock()
    identity = make_identity(clock)

    first = identity.event(clock, "guard.entrance_locked", _DEVICE, {}, actor="human")
    second = identity.event(clock, "guard.entrance_locked", _DEVICE, {}, actor=_DEVICE)

    assert (first.actor, second.actor) == ("human", _DEVICE)
    assert first.id != second.id


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("guard.entrance_bogus", {}),  # Not a declared guard kind.
        ("guard.entrance_denied", {"text": "free prose"}),  # A key the trail refuses.
    ],
)
def test_an_event_the_trail_would_refuse_is_refused_when_built(
    kind: str, payload: dict[str, str]
) -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        make_identity(clock).event(clock, kind, _DEVICE, payload)
