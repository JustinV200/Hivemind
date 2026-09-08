"""Tests for waggle.codec's decode order: every failure's class and code, and their precedence.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Walks Codec.decode's documented order through
    the plain_codec and signed_codec fixtures: size, UTF-8 JSON object with the envelope keys,
    version major, registered kind, signature (missing, malformed, unknown node, tampered,
    wrong key), then payload and envelope validation; one test per failure asserting the error
    class and its stable code, and one per adjacent pair proving which is checked first.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.codec and waggle.codec for the modules under test.
    - waggle.errors for the codes asserted here; docs/waggle/spec.md section 7 for the table.
    - test_codec.py for encode, round trips and canonical bytes.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import (
    FrameTooLargeError,
    InvalidPayloadError,
    InvalidSignatureError,
    MalformedFrameError,
    MissingSignatureError,
    UnknownKindError,
    UnknownSignerError,
    UnsupportedVersionError,
    WaggleError,
)
from waggle.ids import NodeId, new_device_id, new_node_id
from waggle.messages.control.hive import HumanMessage
from waggle.messages.control.protocol import Pong
from waggle.signing import Ed25519Signer

MakeEnvelope = Callable[..., Envelope]

TINY_LIMIT = 64  # Smaller than any real frame, so a normal envelope trips the size check.


def _wire(codec: Codec, envelope: Envelope) -> dict[str, object]:
    """The frame as a dict, so a test can alter one key and re-encode it."""
    parsed: dict[str, object] = json.loads(codec.encode(envelope))
    return parsed


def _frame(wire: dict[str, object]) -> bytes:
    """Re-encode an altered wire dict exactly as the codec would."""
    return json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _expect(codec: Codec, frame: bytes, error: type[WaggleError], code: str) -> WaggleError:
    """Decode ``frame`` expecting ``error`` with ``code``; return it for message assertions."""
    with pytest.raises(error) as caught:
        codec.decode(frame)
    assert caught.value.code == code
    return caught.value


# ──────────────────────────────────────────────────────────────────────────────
# Decode failures, in the documented order
# ──────────────────────────────────────────────────────────────────────────────


def test_decode_rejects_a_frame_over_the_limit_before_parsing_it() -> None:
    error = _expect(
        Codec(max_frame_bytes=TINY_LIMIT),
        b"{" * (TINY_LIMIT + 1),
        FrameTooLargeError,
        "waggle.codec.too_large",
    )

    assert str(TINY_LIMIT + 1) in str(error)


@pytest.mark.parametrize(
    "frame",
    [
        b"\xff\xfe not utf-8",
        b"{not json",
        b"",
        b"[]",
        b"42",
        b'"text"',
        b"null",
        b'{"x": NaN}',
    ],
)
def test_decode_rejects_bytes_that_are_not_a_json_object(plain_codec: Codec, frame: bytes) -> None:
    _expect(plain_codec, frame, MalformedFrameError, "waggle.codec.malformed")


def test_decode_chains_the_parse_failure_as_the_cause(plain_codec: Codec) -> None:
    error = _expect(plain_codec, b"{not json", MalformedFrameError, "waggle.codec.malformed")

    assert isinstance(error.__cause__, json.JSONDecodeError)


def test_decode_rejects_a_missing_envelope_key(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    del wire["node_id"]

    error = _expect(plain_codec, _frame(wire), MalformedFrameError, "waggle.codec.malformed")

    assert "missing ['node_id']" in str(error)


def test_decode_rejects_an_unknown_envelope_key(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["hop_count"] = 1

    error = _expect(plain_codec, _frame(wire), MalformedFrameError, "waggle.codec.malformed")

    assert "unknown ['hop_count']" in str(error)


@pytest.mark.parametrize("version", [1, None, "1", "one.zero", "1.0.0"])
def test_decode_rejects_a_version_that_is_not_major_dot_minor(
    plain_codec: Codec, make_envelope: MakeEnvelope, version: object
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["version"] = version

    _expect(plain_codec, _frame(wire), MalformedFrameError, "waggle.codec.malformed")


def test_decode_rejects_an_unknown_major(plain_codec: Codec, make_envelope: MakeEnvelope) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["version"] = "2.0"

    error = _expect(
        plain_codec, _frame(wire), UnsupportedVersionError, "waggle.version.unsupported_major"
    )

    assert "major 2" in str(error)


def test_decode_accepts_any_minor_of_the_known_major(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["version"] = "1.9"

    assert plain_codec.decode(_frame(wire)).version == "1.9"


def test_decode_rejects_a_kind_that_is_not_a_string(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["kind"] = 7

    _expect(plain_codec, _frame(wire), MalformedFrameError, "waggle.codec.malformed")


def test_decode_rejects_an_unregistered_kind(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["kind"] = "control.nope"

    error = _expect(plain_codec, _frame(wire), UnknownKindError, "waggle.codec.unknown_kind")

    assert "control.nope" in str(error)


def test_signed_codec_rejects_an_unsigned_frame(
    plain_codec: Codec, signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    _expect(
        signed_codec,
        plain_codec.encode(make_envelope()),
        MissingSignatureError,
        "waggle.signature.missing",
    )


def test_signed_codec_rejects_a_signature_that_is_not_text(
    signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(signed_codec, make_envelope())
    wire["signature"] = 5

    _expect(signed_codec, _frame(wire), MalformedFrameError, "waggle.codec.malformed")


def test_signed_codec_rejects_a_node_id_that_is_not_text(
    signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(signed_codec, make_envelope())
    wire["node_id"] = 5

    _expect(signed_codec, _frame(wire), MalformedFrameError, "waggle.codec.malformed")


def test_signed_codec_rejects_a_frame_from_an_unknown_node(
    signed_codec: Codec, make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    stranger: NodeId = new_node_id(fake_clock)
    wire = _wire(signed_codec, make_envelope())
    wire["node_id"] = stranger

    error = _expect(signed_codec, _frame(wire), UnknownSignerError, "waggle.signature.unknown_node")

    assert stranger in str(error)


def test_signed_codec_rejects_a_tampered_frame(
    signed_codec: Codec, make_envelope: MakeEnvelope, warden_address: str, worker_address: str
) -> None:
    # Redirecting a signed order to another bee: the content changes, the signature does not.
    wire = _wire(signed_codec, make_envelope(recipient=warden_address))
    wire["recipient"] = worker_address

    _expect(signed_codec, _frame(wire), InvalidSignatureError, "waggle.signature.invalid")


def test_signed_codec_rejects_a_frame_signed_by_another_key(
    signed_codec: Codec, make_envelope: MakeEnvelope, node_id: NodeId
) -> None:
    impostor = Codec(signer=Ed25519Signer.generate())

    _expect(
        signed_codec,
        impostor.encode(make_envelope()),
        InvalidSignatureError,
        "waggle.signature.invalid",
    )


def test_decode_rejects_a_payload_that_fails_its_model(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["payload"] = {"unexpected": 1}

    error = _expect(plain_codec, _frame(wire), InvalidPayloadError, "waggle.codec.invalid_payload")

    assert str(wire["id"]) in str(error)
    assert "payload" not in str(error) or "unexpected" in str(error)


def test_decode_rejects_a_payload_that_is_not_an_object(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["payload"] = []

    _expect(plain_codec, _frame(wire), InvalidPayloadError, "waggle.codec.invalid_payload")


def test_decode_rejects_an_envelope_that_breaks_the_shape_rule(
    plain_codec: Codec, make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    reply = make_envelope(Pong(received_at=fake_clock.now()), correlation_id=make_envelope().id)
    wire = _wire(plain_codec, reply)
    wire["correlation_id"] = None

    error = _expect(plain_codec, _frame(wire), InvalidPayloadError, "waggle.codec.invalid_payload")

    assert "reply" in str(error)


def test_invalid_payload_error_names_locations_and_never_the_content(
    plain_codec: Codec, make_envelope: MakeEnvelope, fake_clock: FakeClock
) -> None:
    personal = "my bank PIN is 4242"
    human = HumanMessage(text=personal, task_id=None, device_id=new_device_id(fake_clock))
    wire = _wire(plain_codec, make_envelope(human))
    payload = wire["payload"]
    assert isinstance(payload, dict)
    payload["device_id"] = "device_nope"

    error = _expect(plain_codec, _frame(wire), InvalidPayloadError, "waggle.codec.invalid_payload")

    assert "device_id" in str(error)
    assert personal not in str(error)


# ──────────────────────────────────────────────────────────────────────────────
# Precedence between failures
# ──────────────────────────────────────────────────────────────────────────────


def test_size_is_checked_before_shape() -> None:
    _expect(Codec(max_frame_bytes=4), b"{bad!", FrameTooLargeError, "waggle.codec.too_large")


def test_shape_is_checked_before_version(plain_codec: Codec, make_envelope: MakeEnvelope) -> None:
    wire = _wire(plain_codec, make_envelope())
    del wire["sender"]
    wire["version"] = "9.0"

    _expect(plain_codec, _frame(wire), MalformedFrameError, "waggle.codec.malformed")


def test_version_is_checked_before_kind(plain_codec: Codec, make_envelope: MakeEnvelope) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["version"] = "9.0"
    wire["kind"] = "control.nope"

    _expect(plain_codec, _frame(wire), UnsupportedVersionError, "waggle.version.unsupported_major")


def test_kind_is_checked_before_signature(
    plain_codec: Codec, signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["kind"] = "control.nope"

    _expect(signed_codec, _frame(wire), UnknownKindError, "waggle.codec.unknown_kind")


def test_signature_is_checked_before_payload(
    signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(signed_codec, make_envelope())
    wire["payload"] = {"unexpected": 1}

    _expect(signed_codec, _frame(wire), InvalidSignatureError, "waggle.signature.invalid")
