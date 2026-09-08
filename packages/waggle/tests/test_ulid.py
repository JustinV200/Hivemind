"""Property and unit tests for waggle.ulid: the pure ULID encode/decode functions.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.ulid in isolation, with no
    dependency on waggle.ids, waggle.clock or any id-kind concept.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.ulid for the module under test.
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for why ULIDs are hand-rolled here.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from waggle.ulid import CROCKFORD_ALPHABET, ULID_LENGTH, decode_ulid, encode_ulid

# 48-bit millisecond timestamp, per ADR-0003; the largest value encode_ulid accepts.
MAX_TIMESTAMP_MS = (1 << 48) - 1
_timestamps = st.integers(min_value=0, max_value=MAX_TIMESTAMP_MS)
_randomness = st.binary(min_size=10, max_size=10)


@given(timestamp_ms=_timestamps, randomness=_randomness)
def test_encode_ulid_round_trips_through_decode_ulid(timestamp_ms: int, randomness: bytes) -> None:
    encoded = encode_ulid(timestamp_ms, randomness)

    decoded_timestamp_ms, decoded_randomness = decode_ulid(encoded)

    assert decoded_timestamp_ms == timestamp_ms
    assert decoded_randomness == randomness


@given(timestamp_ms=_timestamps, randomness=_randomness)
def test_encode_ulid_uses_only_crockford_alphabet_and_fixed_length(
    timestamp_ms: int, randomness: bytes
) -> None:
    encoded = encode_ulid(timestamp_ms, randomness)

    assert len(encoded) == ULID_LENGTH
    assert all(char in CROCKFORD_ALPHABET for char in encoded)


@given(
    earlier_ms=st.integers(min_value=0, max_value=MAX_TIMESTAMP_MS - 1),
    delta_ms=st.integers(min_value=1, max_value=1000),
    random_a=_randomness,
    random_b=_randomness,
)
def test_encode_ulid_sorts_lexicographically_by_timestamp(
    earlier_ms: int, delta_ms: int, random_a: bytes, random_b: bytes
) -> None:
    later_ms = min(earlier_ms + delta_ms, MAX_TIMESTAMP_MS)
    # Clamping to the 48-bit ceiling can make the two timestamps equal; ordering across equal
    # timestamps depends on the random bits, which this property does not claim to cover.
    if later_ms == earlier_ms:
        return

    earlier_id = encode_ulid(earlier_ms, random_a)
    later_id = encode_ulid(later_ms, random_b)

    assert earlier_id < later_id


def test_encode_ulid_rejects_timestamp_out_of_range() -> None:
    with pytest.raises(ValueError, match="48 bits"):
        encode_ulid(1 << 48, b"0" * 10)


def test_encode_ulid_rejects_wrong_randomness_length() -> None:
    with pytest.raises(ValueError, match="10 bytes"):
        encode_ulid(0, b"short")


def test_decode_ulid_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="26 characters"):
        decode_ulid("TOO_SHORT")


def test_decode_ulid_rejects_bad_character() -> None:
    # "I" is deliberately excluded from Crockford's alphabet (easily confused with 1).
    bad = "I" * ULID_LENGTH

    with pytest.raises(ValueError, match="invalid Crockford base32 character"):
        decode_ulid(bad)
