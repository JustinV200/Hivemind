"""Tests for waggle.codec: the key-free decode steps, exercised directly.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Most of waggle.codec is covered through
    Codec.decode in test_codec.py; this module pins what is easier to see step by step: the key
    set, the strict JSON parse (NaN, deep nesting, chained causes), the version rule on its
    own, and that build_envelope reports locations rather than content.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.codec for the module under test.
    - test_codec.py for the same steps behind Codec.decode.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from waggle.clock import FakeClock
from waggle.codec import ENVELOPE_KEYS, build_envelope, check_kind, check_version, parse_frame
from waggle.envelope import Envelope
from waggle.errors import (
    InvalidPayloadError,
    MalformedFrameError,
    UnknownKindError,
    UnsupportedVersionError,
)
from waggle.ids import new_device_id
from waggle.messages.control.hive import HumanMessage

MakeEnvelope = Callable[..., Envelope]

DEEP_NESTING = 200_000  # Far past CPython's recursion limit: json.loads raises RecursionError.


def _wire(envelope: Envelope) -> dict[str, object]:
    """The raw wire dict a parsed frame of ``envelope`` would give."""
    return envelope.model_dump(mode="json")


def test_envelope_keys_are_exactly_the_envelope_fields() -> None:
    assert frozenset(Envelope.model_fields) == ENVELOPE_KEYS
    assert len(ENVELOPE_KEYS) == 10


def test_parse_frame_returns_the_wire_dict(make_envelope: MakeEnvelope) -> None:

    wire = _wire(make_envelope())

    assert parse_frame(json.dumps(wire).encode()) == wire


def test_parse_frame_treats_deep_nesting_as_malformed_not_a_crash() -> None:
    with pytest.raises(MalformedFrameError) as caught:
        parse_frame(b"[" * DEEP_NESTING)

    assert isinstance(caught.value.__cause__, RecursionError)


def test_parse_frame_rejects_non_json_constants() -> None:
    with pytest.raises(MalformedFrameError) as caught:
        parse_frame(b'{"x": Infinity}')

    assert "Infinity" in str(caught.value.__cause__)


def test_parse_frame_chains_a_unicode_failure() -> None:
    with pytest.raises(MalformedFrameError) as caught:
        parse_frame(b"\xff")

    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.99"])
def test_check_version_accepts_every_minor_of_the_known_major(version: str) -> None:
    check_version({"version": version})


@pytest.mark.parametrize("version", ["0.9", "2.0", "10.0"])
def test_check_version_rejects_every_other_major(version: str) -> None:
    with pytest.raises(UnsupportedVersionError, match=version):
        check_version({"version": version})


@pytest.mark.parametrize("version", [None, 1.0, "1", "1.0.0", "a.b"])
def test_check_version_rejects_a_malformed_version(version: object) -> None:
    with pytest.raises(MalformedFrameError):
        check_version({"version": version})


def test_check_kind_returns_a_registered_kind_and_rejects_the_rest() -> None:
    assert check_kind({"kind": "control.ping"}) == "control.ping"
    with pytest.raises(UnknownKindError):
        check_kind({"kind": "control.nope"})
    with pytest.raises(MalformedFrameError):
        check_kind({"kind": ["control.ping"]})


def test_build_envelope_returns_the_typed_payload(make_envelope: MakeEnvelope) -> None:
    envelope = make_envelope()

    built = build_envelope("control.ping", _wire(envelope))

    assert built == envelope


def test_build_envelope_names_locations_never_content(
    make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    personal = "the operator's real name"
    human = HumanMessage(text=personal, task_id=None, device_id=new_device_id(fake_clock))
    wire = _wire(make_envelope(human))
    payload = wire["payload"]
    assert isinstance(payload, dict)
    payload["text"] = ""  # min_length 1: the failing field is the one holding the content

    with pytest.raises(InvalidPayloadError) as caught:
        build_envelope("control.human_message", wire)

    assert "text" in str(caught.value)
    assert personal not in str(caught.value)


def test_build_envelope_reports_the_raw_ids_on_the_error(make_envelope: MakeEnvelope) -> None:
    wire = _wire(make_envelope())
    wire["payload"] = {"unexpected": 1}  # extra="forbid": a readable id, a payload that fails

    with pytest.raises(InvalidPayloadError) as caught:
        build_envelope("control.ping", wire)

    assert caught.value.message_id == wire["id"]
    assert caught.value.sender == wire["sender"]
    assert caught.value.kind == "control.ping"
    assert caught.value.correlation_id is None


def test_build_envelope_leaves_out_an_id_that_is_not_text(make_envelope: MakeEnvelope) -> None:
    wire = _wire(make_envelope())
    wire["id"] = 5  # parses, but is no id a control.error could correlate to

    with pytest.raises(InvalidPayloadError) as caught:
        build_envelope("control.ping", wire)

    assert caught.value.message_id is None
    assert caught.value.sender == wire["sender"]
