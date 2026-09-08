"""Tests for waggle.envelope: wrap(), every field validator and the shape rule.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.envelope through the
    make_envelope fixture (wrap over a FakeClock): what wrap() fills in, what each validator
    rejects, that an aware non-UTC time is normalised, that the correlation rule follows the
    registered shape, and that the model is frozen, forbids extras and dumps the payload's
    subclass fields.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.envelope for the module under test.
    - waggle.messages.registry for the shapes the correlation rule reads.
    - conftest.py for make_envelope and the address fixtures.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.envelope import (
    BEE_ADDRESS_KINDS,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    PROTOCOL_VERSION,
    Envelope,
    Hop,
    wrap,
)
from waggle.errors import UnknownKindError
from waggle.ids import (
    IdKind,
    MessageId,
    NodeId,
    new_message_id,
    new_task_id,
    parse_id,
    timestamp_of,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.control.protocol import Ping, Pong, Shutdown
from waggle.messages.labels import Urgency

MakeEnvelope = Callable[..., Envelope]

# The envelope fields in spec section 2's order; the codec's frames carry exactly these keys.
SPEC_FIELD_ORDER = [
    "id",
    "correlation_id",
    "sender",
    "recipient",
    "kind",
    "version",
    "sent_at",
    "node_id",
    "payload",
    "signature",
]
FIVE_HOURS_EAST = timezone(timedelta(hours=5))


def _rebuild(envelope: Envelope, **changes: object) -> Envelope:
    """Re-validate ``envelope`` with some fields replaced (model_copy would skip validation)."""
    return Envelope.model_validate(
        {**envelope.model_dump(), "payload": envelope.payload, **changes}
    )


def _shutdown() -> Shutdown:
    """An event-shaped payload, for the correlation tests."""
    return Shutdown(urgency=Urgency.GRACEFUL, deadline_s=30.0, reason="test")


# ──────────────────────────────────────────────────────────────────────────────
# wrap()
# ──────────────────────────────────────────────────────────────────────────────


def test_wrap_fills_every_field(
    make_envelope: MakeEnvelope,
    fake_clock: FakeClock,
    node_id: NodeId,
    queen_address: str,
    warden_address: str,
) -> None:
    envelope = make_envelope()

    assert parse_id(envelope.id, IdKind.MESSAGE) == envelope.id
    assert envelope.correlation_id is None
    assert envelope.sender == queen_address
    assert envelope.recipient == warden_address
    assert envelope.kind == "control.ping"
    assert envelope.version == PROTOCOL_VERSION
    assert envelope.sent_at == fake_clock.now()
    assert envelope.node_id == node_id
    assert envelope.payload == Ping()
    assert envelope.signature is None


def test_wrap_stamps_the_clock_into_both_the_id_and_sent_at(
    make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    # Moved off the default start so an envelope stamped from a fresh clock would be caught.
    fake_clock.advance(12.345)

    envelope = make_envelope()

    # ULIDs carry milliseconds, so the id's time agrees with sent_at at that resolution.
    assert int(timestamp_of(envelope.id).timestamp() * 1000) == int(
        envelope.sent_at.timestamp() * 1000
    )
    assert envelope.sent_at.tzinfo is UTC


def test_wrap_passes_the_correlation_id_through_for_a_reply(
    make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    request = make_envelope()

    reply = make_envelope(Pong(received_at=fake_clock.now()), correlation_id=request.id)

    assert reply.kind == "control.pong"
    assert reply.correlation_id == request.id


def test_wrap_rejects_a_payload_class_that_is_not_registered(
    fake_clock: FakeClock, node_id: NodeId, queen_address: str, warden_address: str
) -> None:
    class Unregistered(WaggleMessage):
        """A payload no kind maps to."""

    hop = Hop(sender=queen_address, recipient=warden_address, node_id=node_id)

    with pytest.raises(UnknownKindError, match="Unregistered"):
        wrap(Unregistered(), hop, clock=fake_clock)


def test_protocol_version_string_is_built_from_the_major_and_minor() -> None:
    assert f"{PROTOCOL_MAJOR}.{PROTOCOL_MINOR}" == PROTOCOL_VERSION


def test_bee_address_kinds_are_the_queen_wardens_workers_and_devices() -> None:
    assert {IdKind.HIVE, IdKind.WARDEN, IdKind.WORKER, IdKind.DEVICE} == BEE_ADDRESS_KINDS


def test_envelope_fields_follow_the_spec_order() -> None:
    assert list(Envelope.model_fields) == SPEC_FIELD_ORDER


def test_hop_is_frozen(node_id: NodeId, queen_address: str, warden_address: str) -> None:
    hop = Hop(sender=queen_address, recipient=warden_address, node_id=node_id)

    with pytest.raises(FrozenInstanceError):
        hop.sender = warden_address  # type: ignore[misc]  # The assignment is the test.


# ──────────────────────────────────────────────────────────────────────────────
# Field validators
# ──────────────────────────────────────────────────────────────────────────────


def test_a_naive_sent_at_is_rejected(make_envelope: MakeEnvelope) -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _rebuild(make_envelope(), sent_at=datetime(2020, 1, 1))  # naive on purpose


def test_an_aware_non_utc_sent_at_is_normalised_to_utc(make_envelope: MakeEnvelope) -> None:
    envelope = _rebuild(make_envelope(), sent_at=datetime(2020, 1, 1, 5, tzinfo=FIVE_HOURS_EAST))

    assert envelope.sent_at == datetime(2020, 1, 1, 0, tzinfo=UTC)
    assert envelope.sent_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("field", ["sender", "recipient"])
def test_an_id_of_a_non_bee_kind_is_rejected_as_an_address(
    field: str, make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValidationError, match="bee address"):
        make_envelope(**{field: new_task_id(fake_clock)})


@pytest.mark.parametrize(
    "address",
    [
        "hive_ABC",  # right prefix, wrong length
        "hive_" + "U" * 26,  # right length, U is outside Crockford's alphabet
        "hive01ARZ3NDEKTSV4RRFFQ69G5FAV",  # no underscore at all
        "",
    ],
)
def test_a_malformed_id_is_rejected_as_an_address(
    address: str, make_envelope: MakeEnvelope
) -> None:
    with pytest.raises(ValidationError, match="ee address"):
        make_envelope(sender=address)


def test_a_bad_node_id_is_rejected(make_envelope: MakeEnvelope) -> None:
    with pytest.raises(ValidationError, match="node_"):
        _rebuild(make_envelope(), node_id="node_nope")


def test_an_id_of_another_kind_is_rejected_as_the_envelope_id(
    make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValidationError, match="msg_"):
        _rebuild(make_envelope(), id=new_task_id(fake_clock))


def test_a_non_message_id_is_rejected_as_correlation_id(
    make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValidationError, match="msg_"):
        _rebuild(make_envelope(_shutdown()), correlation_id=new_task_id(fake_clock))


def test_an_unregistered_kind_is_rejected(make_envelope: MakeEnvelope) -> None:
    with pytest.raises(ValidationError, match="not registered"):
        _rebuild(make_envelope(), kind="control.nope")


def test_a_kind_that_does_not_match_the_payload_class_is_rejected(
    make_envelope: MakeEnvelope,
) -> None:
    with pytest.raises(ValidationError, match="does not match its payload class Ping"):
        _rebuild(make_envelope(), kind="control.shutdown")


@pytest.mark.parametrize("version", ["1", "1.0.0", "v1.0", "", "1.a", " 1.0"])
def test_a_version_outside_the_major_dot_minor_shape_is_rejected(
    version: str, make_envelope: MakeEnvelope
) -> None:
    with pytest.raises(ValidationError, match="version"):
        _rebuild(make_envelope(), version=version)


def test_the_model_accepts_any_well_formed_version(make_envelope: MakeEnvelope) -> None:
    # Rejecting an unknown major is the codec's job (spec section 4), not the model's.
    assert _rebuild(make_envelope(), version="7.3").version == "7.3"


# ──────────────────────────────────────────────────────────────────────────────
# Shape rule (spec section 3)
# ──────────────────────────────────────────────────────────────────────────────


def test_a_reply_without_a_correlation_id_is_rejected(
    make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValidationError, match="is a reply and must carry"):
        make_envelope(Pong(received_at=fake_clock.now()))


def test_a_request_with_a_correlation_id_is_rejected(
    make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValidationError, match="is a request and must not carry"):
        make_envelope(Ping(), correlation_id=new_message_id(fake_clock))


def test_an_event_accepts_a_correlation_id_or_none(
    make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    antecedent: MessageId = new_message_id(fake_clock)

    assert make_envelope(_shutdown()).correlation_id is None
    assert make_envelope(_shutdown(), correlation_id=antecedent).correlation_id == antecedent


# ──────────────────────────────────────────────────────────────────────────────
# Model behaviour
# ──────────────────────────────────────────────────────────────────────────────


def test_envelope_is_frozen(make_envelope: MakeEnvelope) -> None:
    envelope = make_envelope()

    with pytest.raises(ValidationError, match="frozen"):
        envelope.kind = "control.pong"  # type: ignore[misc]  # The assignment is the test.


def test_extra_fields_are_forbidden(make_envelope: MakeEnvelope) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(make_envelope(), hop_count=1)


def test_model_dump_keeps_the_payload_subclass_fields(make_envelope: MakeEnvelope) -> None:
    envelope = make_envelope(_shutdown())

    dumped = envelope.model_dump(mode="json")

    # SerializeAsAny: the Shutdown's own fields, not the empty base class, reach the wire.
    assert dumped["payload"] == {"urgency": "GRACEFUL", "deadline_s": 30.0, "reason": "test"}
    assert isinstance(envelope.payload, Shutdown)
