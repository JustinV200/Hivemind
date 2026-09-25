"""Tests for hivemind.honey_store.identity: HoneyIdentity and honey_event, the one event minter.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/identity.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.identity for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.honey import make_honey_identity
from pydantic import ValidationError

from hivemind.honey_store.identity import HoneyIdentity, honey_event
from waggle.clock import FakeClock
from waggle.ids import new_nectar_id


def test_honey_event_stamps_identity_clock_and_payload() -> None:
    clock = FakeClock()
    identity = make_honey_identity(clock)
    nectar_id = new_nectar_id(clock)

    event = honey_event(identity, clock, "honey.ripened", nectar_id, rows=3, summarised=True)

    assert event.hive_id == identity.hive_id
    assert event.node_id == identity.node_id
    assert event.actor == "system"
    assert event.at == clock.now()
    assert event.kind == "honey.ripened"
    assert event.subject_id == nectar_id
    assert event.payload == {"rows": 3, "summarised": True}


def test_honey_event_mints_a_fresh_id_and_reads_the_clock_each_call() -> None:
    clock = FakeClock()
    identity = make_honey_identity(clock)
    subject = new_nectar_id(clock)

    first = honey_event(identity, clock, "honey.ripened", subject)
    clock.advance(5)
    second = honey_event(identity, clock, "honey.ripened", subject)

    assert first.id != second.id
    assert second.at - first.at == timedelta(seconds=5)


def test_honey_event_accepts_payload_keys_named_like_its_own_parameters_and_keywords() -> None:
    clock = FakeClock()
    identity = make_honey_identity(clock)

    # `kind` and `subject_id` are positional-only, so payload keys may reuse them; `from` is a
    # Python keyword, passed by unpacking a dict as the label events do.
    event = honey_event(
        identity,
        clock,
        "honey.label_raised",
        new_nectar_id(clock),
        kind="FINDING",
        **{"from": "C1", "to": "C2"},
    )

    assert event.payload == {"kind": "FINDING", "from": "C1", "to": "C2"}


def test_honey_event_refuses_a_forbidden_text_payload_key() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        honey_event(
            make_honey_identity(clock),
            clock,
            "honey.queried",
            new_nectar_id(clock),
            text="the query itself must never reach the trail",
        )


def test_honey_event_refuses_an_actor_that_is_not_a_principal() -> None:
    clock = FakeClock()
    identity = make_honey_identity(clock)
    bad = HoneyIdentity(hive_id=identity.hive_id, node_id=identity.node_id, actor="the house bee")

    with pytest.raises(ValidationError):
        honey_event(bad, clock, "honey.ripened", new_nectar_id(clock))


def test_honey_event_refuses_a_kind_outside_the_honey_family() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        honey_event(make_honey_identity(clock), clock, "memory.compacted", new_nectar_id(clock))
