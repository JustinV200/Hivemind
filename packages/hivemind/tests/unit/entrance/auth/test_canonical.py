"""Tests for hivemind.entrance.auth.canonical: the signed strings and the wire encodings.

Each string has a fixed test vector: literal expected bytes a client in any language can check
its own implementation against, and the login string also a deterministic Ed25519 signature.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/canonical.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.canonical for the module under test.
"""

from __future__ import annotations

import base64

import pytest

from hivemind.entrance.auth.canonical import (
    MIN_NONCE_BYTES,
    NONCE_BYTES,
    b64url_decode,
    b64url_encode,
    enrol_string,
    login_string,
    new_nonce,
    path_and_query,
    request_string,
    sha256_hex,
    webhook_string,
    websocket_string,
)
from hivemind.entrance.auth.keys import verify_ed25519
from waggle.signing import Ed25519Signer

_HIVE = "hive_01M221E4C10R4XDPNQNRX85AAA"
_DEVICE = "device_01M221E4C10R4XDPNQNRX85AAA"
_EVENT = "event_01M221E4C10R4XDPNQNRX85AAA"
_NONCE = "AAECAwQFBgcICQoLDA0ODw"  # b64url_encode(bytes(range(16))).
_CODE = "09d86d1ecd87abe2ce28cbfd4310bc89f05079c4a9acfe40a2bcaceef1c99f19"  # SHA-256 of a code.
_PUBLIC_KEY_HEX = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"
_BODY = "7769dbd5df319c908a7ab0f0adaae244dfef701c1625fa853288e8eea9f33cd0"
_SEED = bytes(range(32))  # The private key behind _PUBLIC_KEY_HEX, for the signature vector.
_NOW = 1_790_000_000


# ──────────────────────────────────────────────────────────────────────────────
# Fixed vectors
# ──────────────────────────────────────────────────────────────────────────────


def test_enrol_string_vector() -> None:
    assert enrol_string(_HIVE, _CODE, _PUBLIC_KEY_HEX) == (
        f"hive-enrol-v1\n{_HIVE}\n{_CODE}\n{_PUBLIC_KEY_HEX}".encode()
    )


def test_login_string_vector_and_its_ed25519_signature() -> None:
    message = login_string(_HIVE, _DEVICE, _NONCE)
    signer = Ed25519Signer(_SEED)

    signature = b64url_encode(base64.b64decode(signer.sign(message)))

    assert message == f"hive-login-v1\n{_HIVE}\n{_DEVICE}\n{_NONCE}".encode()
    assert signer.public_key_bytes.hex() == _PUBLIC_KEY_HEX
    assert signature == (
        "Iy08EMbaNCIP4RuXLT4Rs1SA87M1ZJR-hq2jUc_ecr7pfQzx1_iN6xC0-YEuhTQb2w8REqH0Cje_kJf0FfmIDg"
    )
    assert verify_ed25519(signer.public_key_bytes, message, b64url_decode(signature))


def test_request_string_vector_upper_cases_the_method_and_keeps_the_query() -> None:
    target = path_and_query("/v1/goals", "wait=1")

    message = request_string("post", target, _NOW, _NONCE, _BODY)

    assert message == (
        f"hive-request-v1\nPOST\n/v1/goals?wait=1\n{_NOW}\n{_NONCE}\n{_BODY}".encode()
    )


def test_websocket_string_vector() -> None:
    assert websocket_string("/v1/streams/trail", _NOW, _NONCE) == (
        f"hive-ws-v1\n/v1/streams/trail\n{_NOW}\n{_NONCE}".encode()
    )


def test_webhook_string_vector() -> None:
    assert webhook_string("sub_1", _EVENT, _NOW, _BODY) == (
        f"hive-webhook-v1\nsub_1\n{_EVENT}\n{_NOW}\n{_BODY}".encode()
    )


# ──────────────────────────────────────────────────────────────────────────────
# Encodings
# ──────────────────────────────────────────────────────────────────────────────


def test_sha256_hex_of_an_empty_body_is_the_well_known_digest() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_path_and_query_omits_the_question_mark_for_an_empty_query() -> None:
    assert path_and_query("/v1/goals", "") == "/v1/goals"


@pytest.mark.parametrize("data", [b"", b"\x00", b"\xff\xfe", bytes(range(16)), bytes(range(33))])
def test_b64url_round_trips_without_padding(data: bytes) -> None:
    text = b64url_encode(data)

    assert "=" not in text
    assert b64url_decode(text) == data


@pytest.mark.parametrize("text", ["AA==", "A+B/", "A", "AB C", "AB\n", "AB=", "AF", "AAB"])
def test_b64url_decode_refuses_anything_but_the_one_canonical_spelling(text: str) -> None:
    with pytest.raises(ValueError, match="base64url"):
        b64url_decode(text)


def test_new_nonce_draws_fresh_bytes_of_the_requested_size() -> None:
    first, second = new_nonce(), new_nonce()

    assert len(b64url_decode(first)) == NONCE_BYTES
    assert first != second
    assert len(b64url_decode(new_nonce(MIN_NONCE_BYTES))) == MIN_NONCE_BYTES


def test_new_nonce_refuses_a_size_below_the_floor() -> None:
    with pytest.raises(ValueError, match="at least 16"):
        new_nonce(MIN_NONCE_BYTES - 1)


# ──────────────────────────────────────────────────────────────────────────────
# Field refusals
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("hive_id", "code", "key"),
    [
        ("hive_bogus", _CODE, _PUBLIC_KEY_HEX),  # Not a Hive id.
        (_DEVICE, _CODE, _PUBLIC_KEY_HEX),  # A well-formed id of the wrong kind.
        (_HIVE, _CODE.upper(), _PUBLIC_KEY_HEX),  # Upper-case hex is a second spelling.
        (_HIVE, _CODE[:-1], _PUBLIC_KEY_HEX),  # Too short to be a SHA-256.
        (_HIVE, _CODE, _PUBLIC_KEY_HEX + "00"),  # Not a 32-byte key.
    ],
)
def test_enrol_string_refuses_a_malformed_field(hive_id: str, code: str, key: str) -> None:
    with pytest.raises(ValueError):
        enrol_string(hive_id, code, key)


@pytest.mark.parametrize(
    "nonce",
    ["", "AAECAwQFBgcICQoLDA0O", _NONCE + "=", "not base64url!"],  # Empty, 15 bytes, padded, junk.
)
def test_login_string_refuses_a_malformed_or_short_nonce(nonce: str) -> None:
    with pytest.raises(ValueError):
        login_string(_HIVE, _DEVICE, nonce)


def test_login_string_refuses_a_malformed_device_id() -> None:
    with pytest.raises(ValueError, match="device id"):
        login_string(_HIVE, "device_\nforged", _NONCE)


@pytest.mark.parametrize(
    ("method", "target", "timestamp"),
    [
        ("GE T", "/v1/goals", _NOW),  # Not a method token.
        ("", "/v1/goals", _NOW),  # No method at all.
        ("GET", "/v1/goals\nX-Forged: 1", _NOW),  # A line break would forge another line.
        ("GET", "/v1/goals\r", _NOW),  # So would a carriage return.
        ("GET", "", _NOW),  # No target.
        ("GET", "/v1/goals", -1),  # Before the epoch.
        ("GET", "/v1/goals", True),  # A bool is not a timestamp, though it is an int.
    ],
)
def test_request_string_refuses_a_malformed_field(method: str, target: str, timestamp: int) -> None:
    with pytest.raises(ValueError):
        request_string(method, target, timestamp, _NONCE, _BODY)


def test_webhook_string_refuses_a_line_break_in_an_id() -> None:
    with pytest.raises(ValueError, match="subscription id"):
        webhook_string("sub_1\nsub_2", _EVENT, _NOW, _BODY)
