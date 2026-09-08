"""Tests for waggle.signing: Ed25519Signer and Ed25519Verifier.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises waggle.signing in isolation with keys
    generated per test: the sign/verify round trip, every way verification must fail, the
    verifier's immutability, key-length validation, and the promise that no repr or error message
    leaks key material. The hypothesis properties pin the round trip and tamper detection over
    arbitrary bytes, standing in for what waggle.codec will feed this module.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.signing for the module under test.
    - waggle.errors for the SignatureError family every verification failure here raises.
    - test_key_encoding.py for the hex form of a public key.
"""

from __future__ import annotations

import base64

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from waggle.errors import InvalidSignatureError, SignatureError, UnknownSignerError
from waggle.signing import (
    PRIVATE_KEY_BYTES,
    PUBLIC_KEY_BYTES,
    SIGNATURE_BYTES,
    Ed25519Signer,
    Ed25519Verifier,
)

# Stands in for waggle.codec.canonical_bytes output: any bytes will do for signing, and a fixed
# value keeps the unit tests readable; the hypothesis properties at the end cover arbitrary bytes.
CANONICAL = b'{"id":"msg_01ARZ3NDEKTSV4RRFFQ69G5FAV","kind":"control.ping"}'
NODE_ID = "node_01ARZ3NDEKTSV4RRFFQ69G5FAV"
OTHER_NODE_ID = "node_01BX5ZZKBKACTAV9WEVGEMMVRZ"
REPR_PREFIX_BYTES = 8  # How much public key the signer's repr is allowed to show.


def _verifier_for(signer: Ed25519Signer, node_id: str = NODE_ID) -> Ed25519Verifier:
    """Build a verifier trusting exactly one signer under ``node_id``."""
    return Ed25519Verifier({node_id: signer.public_key_bytes})


# ──────────────────────────────────────────────────────────────────────────────
# Sign and verify
# ──────────────────────────────────────────────────────────────────────────────


def test_verifier_accepts_a_signature_the_signer_produced_over_the_same_bytes() -> None:
    signer = Ed25519Signer.generate()
    verifier = _verifier_for(signer)

    signature = signer.sign(CANONICAL)

    # Success is silence: verify is declared to return None, so returning at all is the pass.
    verifier.verify(NODE_ID, CANONICAL, signature)


def test_sign_returns_padded_standard_base64_of_a_sixty_four_byte_signature() -> None:
    signer = Ed25519Signer.generate()

    signature = signer.sign(CANONICAL)

    # validate=True makes the decoder reject anything outside the standard alphabet, so this also
    # proves the text is neither urlsafe base64 (which uses "-" and "_") nor unpadded.
    assert len(base64.b64decode(signature, validate=True)) == SIGNATURE_BYTES
    assert signature.isascii()


def test_sign_is_deterministic_for_the_same_key_and_bytes() -> None:
    signer = Ed25519Signer.generate()

    assert signer.sign(CANONICAL) == signer.sign(CANONICAL)


def test_two_signers_never_agree_on_a_signature() -> None:
    first = Ed25519Signer.generate()
    second = Ed25519Signer.generate()

    assert first.sign(CANONICAL) != second.sign(CANONICAL)


def test_verify_rejects_a_single_tampered_byte_of_the_canonical_bytes() -> None:
    signer = Ed25519Signer.generate()
    verifier = _verifier_for(signer)
    signature = signer.sign(CANONICAL)
    # Flip one bit of one byte in the middle: the smallest change a relay could make.
    tampered = bytearray(CANONICAL)
    tampered[len(tampered) // 2] ^= 0x01

    with pytest.raises(InvalidSignatureError, match=NODE_ID):
        verifier.verify(NODE_ID, bytes(tampered), signature)


def test_verify_rejects_a_signature_made_with_another_nodes_key() -> None:
    trusted = Ed25519Signer.generate()
    impostor = Ed25519Signer.generate()
    verifier = _verifier_for(trusted)

    with pytest.raises(InvalidSignatureError, match=NODE_ID):
        verifier.verify(NODE_ID, CANONICAL, impostor.sign(CANONICAL))


def test_verify_rejects_a_node_it_holds_no_key_for() -> None:
    signer = Ed25519Signer.generate()
    verifier = _verifier_for(signer)

    with pytest.raises(UnknownSignerError, match=OTHER_NODE_ID):
        verifier.verify(OTHER_NODE_ID, CANONICAL, signer.sign(CANONICAL))


@pytest.mark.parametrize(
    "malformed",
    [
        "not base64!",  # characters outside the standard alphabet
        "abc",  # valid characters, missing padding
        "AAA=AAAA",  # padding in the middle
        "ÿÿÿÿ",  # non-ASCII text never reaches the decoder
    ],
)
def test_verify_rejects_signature_text_that_is_not_base64(malformed: str) -> None:
    verifier = _verifier_for(Ed25519Signer.generate())

    with pytest.raises(InvalidSignatureError, match="not valid base64"):
        verifier.verify(NODE_ID, CANONICAL, malformed)


@pytest.mark.parametrize("length", [0, SIGNATURE_BYTES - 1, SIGNATURE_BYTES + 1])
def test_verify_rejects_a_signature_of_the_wrong_length(length: int) -> None:
    verifier = _verifier_for(Ed25519Signer.generate())
    wrong_length = base64.b64encode(bytes(length)).decode("ascii")

    with pytest.raises(InvalidSignatureError, match=f"decodes to {length} bytes"):
        verifier.verify(NODE_ID, CANONICAL, wrong_length)


def test_both_verification_failures_are_signature_errors() -> None:
    # Transports catch SignatureError alone to close the link on any bad frame, so both the
    # unknown-node and the bad-signature paths must sit under that one root.
    signer = Ed25519Signer.generate()
    verifier = _verifier_for(signer)

    with pytest.raises(SignatureError):
        verifier.verify(OTHER_NODE_ID, CANONICAL, signer.sign(CANONICAL))
    with pytest.raises(SignatureError):
        verifier.verify(NODE_ID, CANONICAL, "not base64!")


def test_verification_errors_name_the_node_and_never_the_signature_or_key() -> None:
    signer = Ed25519Signer.generate()
    verifier = _verifier_for(signer)
    signature = signer.sign(b"some other canonical bytes")

    with pytest.raises(InvalidSignatureError) as caught:
        verifier.verify(NODE_ID, CANONICAL, signature)

    message = str(caught.value)
    assert NODE_ID in message
    assert signature not in message
    assert signer.public_key_bytes.hex() not in message


# ──────────────────────────────────────────────────────────────────────────────
# Verifier immutability
# ──────────────────────────────────────────────────────────────────────────────


def test_with_key_returns_a_new_verifier_and_leaves_the_old_one_unchanged() -> None:
    first = Ed25519Signer.generate()
    second = Ed25519Signer.generate()
    original = _verifier_for(first)

    extended = original.with_key(OTHER_NODE_ID, second.public_key_bytes)

    assert extended is not original
    assert original.known_nodes == frozenset({NODE_ID})
    assert extended.known_nodes == frozenset({NODE_ID, OTHER_NODE_ID})
    extended.verify(OTHER_NODE_ID, CANONICAL, second.sign(CANONICAL))
    with pytest.raises(UnknownSignerError):
        original.verify(OTHER_NODE_ID, CANONICAL, second.sign(CANONICAL))


def test_with_key_replaces_the_key_already_held_for_that_node() -> None:
    old_key = Ed25519Signer.generate()
    new_key = Ed25519Signer.generate()
    verifier = _verifier_for(old_key)

    # Key rotation: same node id, new public key; the old key's signatures must stop verifying.
    rotated = verifier.with_key(NODE_ID, new_key.public_key_bytes)

    rotated.verify(NODE_ID, CANONICAL, new_key.sign(CANONICAL))
    with pytest.raises(InvalidSignatureError):
        rotated.verify(NODE_ID, CANONICAL, old_key.sign(CANONICAL))


def test_verifier_copies_the_mapping_it_was_given() -> None:
    signer = Ed25519Signer.generate()
    keys = {NODE_ID: signer.public_key_bytes}
    verifier = Ed25519Verifier(keys)

    # Mutating the caller's dict after construction must not change what the verifier trusts.
    keys[OTHER_NODE_ID] = Ed25519Signer.generate().public_key_bytes

    assert verifier.known_nodes == frozenset({NODE_ID})


def test_empty_verifier_treats_every_node_as_unknown() -> None:
    verifier = Ed25519Verifier({})
    signer = Ed25519Signer.generate()

    assert verifier.known_nodes == frozenset()
    with pytest.raises(UnknownSignerError):
        verifier.verify(NODE_ID, CANONICAL, signer.sign(CANONICAL))


# ──────────────────────────────────────────────────────────────────────────────
# Key material: lengths, generation, persistence and repr
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("length", [0, PRIVATE_KEY_BYTES - 1, PRIVATE_KEY_BYTES + 1])
def test_signer_rejects_a_private_key_of_the_wrong_length(length: int) -> None:
    with pytest.raises(ValueError, match=f"exactly {PRIVATE_KEY_BYTES} raw bytes, got {length}"):
        Ed25519Signer(bytes(length))


@pytest.mark.parametrize("length", [0, PUBLIC_KEY_BYTES - 1, PUBLIC_KEY_BYTES + 1])
def test_verifier_rejects_a_public_key_of_the_wrong_length(length: int) -> None:
    good = Ed25519Signer.generate().public_key_bytes

    # Both construction paths validate, and the message names the node whose key is wrong.
    with pytest.raises(ValueError, match=f"{OTHER_NODE_ID} must be exactly .* got {length}"):
        Ed25519Verifier({NODE_ID: good, OTHER_NODE_ID: bytes(length)})
    with pytest.raises(ValueError, match=f"{OTHER_NODE_ID} must be exactly .* got {length}"):
        Ed25519Verifier({NODE_ID: good}).with_key(OTHER_NODE_ID, bytes(length))


def test_generate_produces_a_distinct_keypair_every_time() -> None:
    first = Ed25519Signer.generate()
    second = Ed25519Signer.generate()

    assert first.public_key_bytes != second.public_key_bytes
    assert first.private_key_bytes != second.private_key_bytes


def test_private_key_bytes_rebuild_the_same_signer() -> None:
    # The composition-root path: persist the raw key, restart, load it back.
    original = Ed25519Signer.generate()

    reloaded = Ed25519Signer(original.private_key_bytes)

    assert len(original.private_key_bytes) == PRIVATE_KEY_BYTES
    assert len(original.public_key_bytes) == PUBLIC_KEY_BYTES
    assert reloaded.public_key_bytes == original.public_key_bytes
    assert reloaded.sign(CANONICAL) == original.sign(CANONICAL)


def test_signer_repr_shows_a_public_key_prefix_and_no_key_material() -> None:
    signer = Ed25519Signer.generate()

    text = repr(signer)

    assert text.startswith("Ed25519Signer(public_key=")
    assert signer.public_key_bytes[:REPR_PREFIX_BYTES].hex() in text
    # Neither key, in any encoding a log scraper could reassemble.
    for key in (signer.private_key_bytes, signer.public_key_bytes):
        assert key.hex() not in text
        assert base64.b64encode(key).decode("ascii") not in text
        assert repr(key) not in text


# ──────────────────────────────────────────────────────────────────────────────
# Properties over arbitrary canonical bytes
# ──────────────────────────────────────────────────────────────────────────────


@settings(max_examples=50)
@given(canonical=st.binary())
def test_any_canonical_bytes_round_trip_through_sign_and_verify(canonical: bytes) -> None:
    signer = Ed25519Signer.generate()
    verifier = _verifier_for(signer)

    # Empty bytes included: Ed25519 signs the empty message like any other.
    verifier.verify(NODE_ID, canonical, signer.sign(canonical))


@settings(max_examples=50)
@given(
    canonical=st.binary(min_size=1),
    position=st.integers(min_value=0),
    flip=st.integers(min_value=1, max_value=255),
)
def test_any_flipped_byte_of_the_canonical_bytes_fails_verification(
    canonical: bytes, position: int, flip: int
) -> None:
    signer = Ed25519Signer.generate()
    verifier = _verifier_for(signer)
    signature = signer.sign(canonical)
    # XOR with a non-zero mask always changes the byte; the position wraps so any draw is in
    # range, which lets hypothesis shrink both freely.
    tampered = bytearray(canonical)
    tampered[position % len(tampered)] ^= flip

    with pytest.raises(InvalidSignatureError):
        verifier.verify(NODE_ID, bytes(tampered), signature)
