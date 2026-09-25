"""Tests for hivemind.entrance.push.web_push.ece: RFC 8291 aes128gcm, both directions.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/web_push/ece.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.web_push.ece for the module under test.
    - RFC 8291 Appendix A for the test vector reproduced here byte for byte.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from unit.entrance.push.support import UserAgent

from hivemind.entrance.auth import b64url_decode
from hivemind.entrance.push.web_push import (
    HEADER_BYTES,
    TAG_BYTES,
    ReceiverKeys,
    SenderMaterial,
    decrypt,
    encrypt,
)

# RFC 8291 Appendix A, every value base64url without padding, exactly as the RFC prints them.
_PLAINTEXT = "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"
_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
_AS_PUBLIC = (
    "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
)
_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
_UA_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
_UA_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS"
    "6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qu"
    "lcy4a-fN"
)
_PADDED = 512  # The notice channel's padded length, for the round-trip tests.


def _private_key(scalar: str) -> ec.EllipticCurvePrivateKey:
    """Rebuild a P-256 private key from the RFC's base64url scalar."""
    return ec.derive_private_key(int.from_bytes(b64url_decode(scalar), "big"), ec.SECP256R1())


def _rfc_receiver() -> ReceiverKeys:
    """The RFC's user agent: its public key and auth secret."""
    return ReceiverKeys(public_key=b64url_decode(_UA_PUBLIC), auth_secret=b64url_decode(_UA_AUTH))


def test_encrypt_reproduces_the_rfc_8291_appendix_a_body_byte_for_byte() -> None:
    # The seam pins the RFC's ephemeral key and salt; the record is the content plus its delimiter.
    plaintext = b64url_decode(_PLAINTEXT)
    material = SenderMaterial(private_key=_private_key(_AS_PRIVATE), salt=b64url_decode(_SALT))

    body = encrypt(plaintext, _rfc_receiver(), len(plaintext) + 1, material)

    assert body == b64url_decode(_BODY)


def test_decrypt_opens_the_rfc_8291_appendix_a_body_to_its_plaintext() -> None:
    body = b64url_decode(_BODY)

    plaintext = decrypt(body, _private_key(_UA_PRIVATE), b64url_decode(_UA_AUTH))

    assert plaintext == b"When I grow up, I want to be a watermelon"
    assert plaintext == b64url_decode(_PLAINTEXT)


def test_encrypt_puts_the_sender_key_and_salt_in_the_header() -> None:
    plaintext = b64url_decode(_PLAINTEXT)
    material = SenderMaterial(private_key=_private_key(_AS_PRIVATE), salt=b64url_decode(_SALT))

    body = encrypt(plaintext, _rfc_receiver(), len(plaintext) + 1, material)

    assert body[:16] == b64url_decode(_SALT)
    assert body[16:20] == (4096).to_bytes(4, "big")
    assert body[20] == 65
    assert body[21:HEADER_BYTES] == b64url_decode(_AS_PUBLIC)


def test_encrypt_round_trips_through_the_user_agent() -> None:
    agent = UserAgent()
    receiver = ReceiverKeys(
        public_key=b64url_decode(agent.keys.p256dh), auth_secret=agent.auth_secret
    )

    body = encrypt(b'{"kind":"question_waiting"}', receiver, _PADDED)

    assert agent.open(body) == b'{"kind":"question_waiting"}'


def test_encrypt_pads_every_content_to_one_body_length() -> None:
    agent = UserAgent()
    receiver = ReceiverKeys(
        public_key=b64url_decode(agent.keys.p256dh), auth_secret=agent.auth_secret
    )

    lengths = {len(encrypt(b"x" * size, receiver, _PADDED)) for size in (0, 1, 100, 511)}

    assert lengths == {HEADER_BYTES + _PADDED + TAG_BYTES}


def test_encrypt_uses_fresh_material_for_every_message() -> None:
    agent = UserAgent()
    receiver = ReceiverKeys(
        public_key=b64url_decode(agent.keys.p256dh), auth_secret=agent.auth_secret
    )

    first = encrypt(b"same", receiver, _PADDED)
    second = encrypt(b"same", receiver, _PADDED)

    # Different salt and ephemeral key, so different bytes everywhere, not just in the header.
    assert first[:HEADER_BYTES] != second[:HEADER_BYTES]
    assert first[HEADER_BYTES:] != second[HEADER_BYTES:]


@pytest.mark.parametrize("padded_length", [5, 4, 4081])
def test_encrypt_rejects_a_padded_length_that_cannot_hold_the_record(padded_length: int) -> None:
    with pytest.raises(ValueError, match="padded record"):
        encrypt(b"12345", _rfc_receiver(), padded_length)


def test_encrypt_rejects_an_auth_secret_of_the_wrong_length() -> None:
    receiver = ReceiverKeys(public_key=b64url_decode(_UA_PUBLIC), auth_secret=b"short")

    with pytest.raises(ValueError, match="auth secret"):
        encrypt(b"x", receiver, _PADDED)


def test_decrypt_rejects_a_body_altered_anywhere() -> None:
    body = bytearray(b64url_decode(_BODY))
    body[-1] ^= 0x01

    with pytest.raises(ValueError, match="does not decrypt"):
        decrypt(bytes(body), _private_key(_UA_PRIVATE), b64url_decode(_UA_AUTH))


def test_decrypt_rejects_the_wrong_auth_secret() -> None:
    with pytest.raises(ValueError, match="does not decrypt"):
        decrypt(b64url_decode(_BODY), _private_key(_UA_PRIVATE), bytes(16))


def test_decrypt_rejects_a_body_too_short_for_a_header() -> None:
    with pytest.raises(ValueError, match="too short"):
        decrypt(b"\x00" * 40, _private_key(_UA_PRIVATE), b64url_decode(_UA_AUTH))


def test_decrypt_rejects_a_key_id_that_is_not_a_p256_point() -> None:
    body = bytearray(b64url_decode(_BODY))
    body[20] = 64

    with pytest.raises(ValueError, match="key id"):
        decrypt(bytes(body), _private_key(_UA_PRIVATE), b64url_decode(_UA_AUTH))


def test_decrypt_rejects_a_body_longer_than_one_record() -> None:
    body = bytearray(b64url_decode(_BODY))
    body[16:20] = (18).to_bytes(4, "big")

    with pytest.raises(ValueError, match="more than one record"):
        decrypt(bytes(body), _private_key(_UA_PRIVATE), b64url_decode(_UA_AUTH))
