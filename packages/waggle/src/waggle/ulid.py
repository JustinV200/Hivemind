"""Encode and decode ULIDs (sortable, timestamped ids) with the standard library only.

A ULID packs a 48-bit millisecond timestamp and 80 bits of randomness into one 128-bit value,
then renders it as 26 Crockford base32 characters. Because the timestamp occupies the high-order
bits, two ULIDs sort the same way lexicographically (as plain strings) as they do numerically (by
creation time), which is what lets ``waggle.ids`` build ids that are self-describing in a log line
and naturally ordered without decoding them. This module knows nothing about ids, prefixes, or the
Clock (the injected source of time every Hive component reads instead of calling the system clock
directly, so tests can run at simulated speed); it is pure encode/decode, kept separate so
``waggle/ids.py`` (which adds prefixes, NewTypes and a Clock-driven generator) stays under the
project's file-size limit.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Called only by waggle.ids; calls into nothing else in
    the workspace.

Key invariants:
    - encode_ulid always returns exactly ULID_LENGTH characters, each drawn from
      CROCKFORD_ALPHABET.
    - For any two valid (timestamp_ms, randomness) pairs with different timestamps, the encoded
      string with the larger timestamp sorts later as a plain string, regardless of the
      randomness bits.
    - decode_ulid(encode_ulid(ts, rand)) == (ts, rand) for every valid input.

See Also:
    - docs/adr/0003-ids-clock-and-loop-live-in-waggle.md for why this encoding is hand-rolled
      instead of pulled from a third-party ULID library.
    - waggle.ids for the prefixed, typed ids built on top of this encoding.
"""

from __future__ import annotations

# Crockford's base32 alphabet: excludes I, L, O and U so a human transcribing an id by hand never
# confuses a letter with 1 or 0, and never accidentally spells a profanity (the reason U is out).
CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ALPHABET_INDEX = {char: index for index, char in enumerate(CROCKFORD_ALPHABET)}

TIMESTAMP_BITS = 48  # Milliseconds since the epoch; 2^48 ms is well past the year 10,000.
RANDOMNESS_BITS = 80  # Entropy bits per id; astronomically unlikely to collide within one ms.
RANDOMNESS_BYTES = RANDOMNESS_BITS // 8  # 10 bytes of randomness, as drawn from `secrets`.
ULID_LENGTH = 26  # ceil(128 bits / 5 bits-per-base32-char); the top char carries only 2 real bits.

__all__ = ["CROCKFORD_ALPHABET", "ULID_LENGTH", "decode_ulid", "encode_ulid"]


def encode_ulid(timestamp_ms: int, randomness: bytes) -> str:
    r"""Encode a millisecond timestamp and 80 bits of randomness as a 26-character ULID.

    Args:
        timestamp_ms: Milliseconds since the Unix epoch. Must fit in TIMESTAMP_BITS bits
            (0 <= timestamp_ms < 2**48).
        randomness: Exactly RANDOMNESS_BYTES (10) bytes of entropy.

    Returns:
        A 26-character string drawn from CROCKFORD_ALPHABET. Lexicographic order matches the
        numeric order of ``timestamp_ms`` for any two ids with different timestamps.

    Raises:
        ValueError: ``timestamp_ms`` is negative or does not fit in 48 bits, or ``randomness``
            is not exactly 10 bytes.

    Example:
        >>> encode_ulid(0, b"\\x00" * 10)
        '0000000000000000000000000'
    """
    # Both bounds are checked up front so a caller gets one clear error instead of a silently
    # truncated or corrupted id further down.
    if not 0 <= timestamp_ms < (1 << TIMESTAMP_BITS):
        raise ValueError(f"timestamp_ms {timestamp_ms} does not fit in {TIMESTAMP_BITS} bits")
    if len(randomness) != RANDOMNESS_BYTES:
        raise ValueError(f"randomness must be {RANDOMNESS_BYTES} bytes, got {len(randomness)}")

    # Packing both fields into one integer, timestamp in the high bits, is what gives the
    # resulting base32 string its sort-by-creation-time property: reading 5 bits at a time from
    # the top down visits the timestamp's bits before any randomness bit.
    value = (timestamp_ms << RANDOMNESS_BITS) | int.from_bytes(randomness, "big")

    # 26 chars * 5 bits = 130 bits, two more than the 128 we packed, so the first character's top
    # two bits are always zero for a value built this way; that is expected, not an error.
    return "".join(
        CROCKFORD_ALPHABET[(value >> (5 * (ULID_LENGTH - 1 - position))) & 0x1F]
        for position in range(ULID_LENGTH)
    )


def decode_ulid(ulid: str) -> tuple[int, bytes]:
    r"""Decode a 26-character ULID back into its timestamp and randomness.

    Args:
        ulid: A string produced by encode_ulid (or an equally-shaped candidate to validate).

    Returns:
        A ``(timestamp_ms, randomness)`` pair equal to whatever encode_ulid was originally
        called with.

    Raises:
        ValueError: ``ulid`` is not exactly ULID_LENGTH characters, or contains a character
            outside CROCKFORD_ALPHABET.

    Example:
        >>> decode_ulid("0000000000000000000000000")
        (0, b'\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00')
    """
    if len(ulid) != ULID_LENGTH:
        raise ValueError(f"ulid must be {ULID_LENGTH} characters, got {len(ulid)}")

    # Rebuild the packed integer 5 bits at a time, in the same left-to-right order encode_ulid
    # produced it in, so the shift below is the exact inverse of the one in encode_ulid.
    value = 0
    for char in ulid:
        digit = _ALPHABET_INDEX.get(char)
        if digit is None:
            raise ValueError(f"invalid Crockford base32 character {char!r} in ulid {ulid!r}")
        value = (value << 5) | digit

    # Split the 128 meaningful bits back into timestamp (high) and randomness (low), undoing the
    # pack in encode_ulid.
    timestamp_ms = value >> RANDOMNESS_BITS
    randomness = (value & ((1 << RANDOMNESS_BITS) - 1)).to_bytes(RANDOMNESS_BYTES, "big")
    return timestamp_ms, randomness
