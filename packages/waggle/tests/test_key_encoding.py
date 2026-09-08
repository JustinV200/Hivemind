"""Tests for waggle.key_encoding: the hex form of an Ed25519 public key.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises the two hex helpers against real keys
    from waggle.signing: the round trip in either case, and the length and character checks that
    keep a mistyped manifest value from silently becoming a wrong key.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.key_encoding for the module under test.
    - test_signing.py for the signer whose public key these helpers render.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from waggle.key_encoding import public_key_from_hex, public_key_hex
from waggle.signing import PUBLIC_KEY_BYTES, Ed25519Signer


def test_public_key_hex_is_sixty_four_lowercase_characters_that_parse_back() -> None:
    public_key = Ed25519Signer.generate().public_key_bytes

    text = public_key_hex(public_key)

    assert len(text) == PUBLIC_KEY_BYTES * 2
    assert text == text.lower()
    assert public_key_from_hex(text) == public_key


def test_public_key_from_hex_accepts_upper_case_as_an_operator_might_type_it() -> None:
    public_key = Ed25519Signer.generate().public_key_bytes

    assert public_key_from_hex(public_key_hex(public_key).upper()) == public_key


@pytest.mark.parametrize("length", [0, PUBLIC_KEY_BYTES - 1, PUBLIC_KEY_BYTES + 1])
def test_public_key_hex_helpers_reject_the_wrong_length(length: int) -> None:
    with pytest.raises(ValueError, match=f"exactly {PUBLIC_KEY_BYTES} raw bytes, got {length}"):
        public_key_hex(bytes(length))
    with pytest.raises(ValueError, match=f"exactly {PUBLIC_KEY_BYTES} raw bytes, got {length}"):
        public_key_from_hex("00" * length)


def test_public_key_from_hex_rejects_non_hex_text_without_echoing_it() -> None:
    # The text is left out of the message on purpose: it might be a pasted private key.
    not_hex = "zz" * PUBLIC_KEY_BYTES

    with pytest.raises(ValueError, match="only hex digits") as caught:
        public_key_from_hex(not_hex)

    assert not_hex not in str(caught.value)


@settings(max_examples=50)
@given(public_key=st.binary(min_size=PUBLIC_KEY_BYTES, max_size=PUBLIC_KEY_BYTES))
def test_any_thirty_two_bytes_round_trip_through_hex(public_key: bytes) -> None:
    # The helpers encode bytes, not curve points, so every 32-byte value must survive the trip.
    assert public_key_from_hex(public_key_hex(public_key)) == public_key
