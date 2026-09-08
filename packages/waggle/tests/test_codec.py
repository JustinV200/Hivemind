"""Tests for waggle.codec: the wire shape of a frame, round trips, the size limit, canonical bytes.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises Codec.encode and the happy decode
    path through the plain_codec and signed_codec fixtures: the frame's JSON shape, the round
    trip under both signature policies, signature replacement, the size limit on encode, and
    canonical_bytes. Every decode failure and the precedence between them live in
    test_codec_decode.py; the hypothesis properties in test_codec_properties.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.codec for the module under test.
    - test_codec_decode.py and test_codec_properties.py for the rest of its suite.
    - conftest.py for the codec, signer and envelope fixtures.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from waggle.codec import (
    ENVELOPE_KEYS,
    MAX_FRAME_BYTES,
    SIGNATURE_OVERHEAD_BYTES,
    Codec,
    canonical_bytes,
)
from waggle.envelope import Envelope
from waggle.errors import FrameTooLargeError
from waggle.messages.base import WaggleMessage

MakeEnvelope = Callable[..., Envelope]

TINY_LIMIT = 64  # Smaller than any real frame, so a normal envelope trips the size check.
TWO_MEBIBYTES = 2 * MAX_FRAME_BYTES
SIGNATURE_B64_CHARS = 88  # A 64-byte Ed25519 signature in padded standard base64.


class _Oversized(WaggleMessage):
    """A payload with no bound, to build a frame no registered kind could legally produce."""

    text: str


def _wire(codec: Codec, envelope: Envelope) -> dict[str, object]:
    """The frame as a dict, so a test can alter one key and re-encode it."""
    parsed: dict[str, object] = json.loads(codec.encode(envelope))
    return parsed


def _frame(wire: dict[str, object]) -> bytes:
    """Re-encode an altered wire dict exactly as the codec would."""
    return json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ──────────────────────────────────────────────────────────────────────────────
# Encode and round trip
# ──────────────────────────────────────────────────────────────────────────────


def test_encode_produces_compact_sorted_json_with_exactly_the_envelope_keys(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    frame = plain_codec.encode(make_envelope())

    wire = json.loads(frame)
    assert set(wire) == ENVELOPE_KEYS
    assert list(wire) == sorted(wire)
    assert frame == _frame(wire)
    assert wire["signature"] is None


def test_plain_round_trip_returns_an_equal_envelope(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope()

    assert plain_codec.decode(plain_codec.encode(envelope)) == envelope


def test_signed_round_trip_carries_a_signature_and_equal_content(
    signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope()

    decoded = signed_codec.decode(signed_codec.encode(envelope))

    assert decoded.signature is not None
    assert len(decoded.signature) == SIGNATURE_B64_CHARS
    assert decoded.model_dump(exclude={"signature"}) == envelope.model_dump(exclude={"signature"})


def test_encode_replaces_any_signature_the_envelope_carries(
    plain_codec: Codec, signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    stale = make_envelope().model_copy(update={"signature": "stale"})

    assert _wire(plain_codec, stale)["signature"] is None
    assert _wire(signed_codec, stale)["signature"] not in (None, "stale")


def test_plain_codec_ignores_whatever_signature_a_frame_carries(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    wire = _wire(plain_codec, make_envelope())
    wire["signature"] = "not even base64"

    assert plain_codec.decode(_frame(wire)).signature == "not even base64"


def test_signature_overhead_bounds_what_signing_adds_to_a_frame(
    plain_codec: Codec, signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope()

    delta = len(signed_codec.encode(envelope)) - len(plain_codec.encode(envelope))

    # The unsigned frame already spends `"signature":null`, so the real delta is the bound minus
    # that member's bytes (with its comma); the bound itself stays an upper limit.
    assert delta == SIGNATURE_OVERHEAD_BYTES - len(',"signature":null')
    assert delta <= SIGNATURE_OVERHEAD_BYTES


def test_encode_refuses_a_frame_over_the_limit(make_envelope: MakeEnvelope) -> None:
    envelope = make_envelope()

    with pytest.raises(FrameTooLargeError, match=f"over the limit of {TINY_LIMIT}") as caught:
        Codec(max_frame_bytes=TINY_LIMIT).encode(envelope)

    assert caught.value.code == "waggle.codec.too_large"
    assert envelope.id in str(caught.value)


def test_a_two_mebibyte_payload_never_encodes(
    plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    # No registered kind can carry 2 MiB (every text field is bounded), so validation is
    # bypassed on purpose to prove the size check holds even for a payload that slips past it.
    fields = make_envelope().model_dump()
    fields["payload"] = _Oversized(text="x" * TWO_MEBIBYTES)
    oversized = Envelope.model_construct(**fields)

    with pytest.raises(FrameTooLargeError, match=str(MAX_FRAME_BYTES)):
        plain_codec.encode(oversized)


# ──────────────────────────────────────────────────────────────────────────────
# canonical_bytes
# ──────────────────────────────────────────────────────────────────────────────


def test_canonical_bytes_drop_the_signature_sort_keys_and_escape_non_ascii() -> None:
    canonical = canonical_bytes({"signature": "x", "b": "é", "a": [1, {"d": 2, "c": 3}]})

    assert canonical == b'{"a":[1,{"c":3,"d":2}],"b":"\\u00e9"}'
    assert canonical.isascii()


def test_canonical_bytes_are_the_same_for_the_sent_and_the_parsed_wire(
    signed_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    envelope = make_envelope()
    sent = envelope.model_dump(mode="json", exclude={"signature"})

    parsed = _wire(signed_codec, envelope)

    assert canonical_bytes(parsed) == canonical_bytes(sent)
