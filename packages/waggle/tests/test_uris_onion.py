"""Tests for waggle.uris's onion-service rule: plaintext ws:// to a v3 onion service (10.3a).

An onion address is the service's own public key, and Tor encrypts and authenticates the link
end to end, so a `ws://` URI on a well-formed v3 onion service is dialable with no opt-in; any
other non-loopback host still is not, and a malformed or mistyped onion address is refused.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/waggle/uris.py (codingrules 5.1:
    split by feature from test_uris.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.uris for check_waggle_uri and is_onion_service_host.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from waggle.uris import check_waggle_uri, is_onion_service_host

ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.


def _onion(key: bytes, version: int = 3, *, checksum_ok: bool = True) -> str:
    """Build a v3-shaped onion address from a 32-byte key (rend-spec-v3's encoding)."""
    digest = hashlib.sha3_256(b".onion checksum" + key + bytes((version,))).digest()[:2]
    checksum = digest if checksum_ok else bytes(b ^ 0xFF for b in digest)
    return base64.b32encode(key + checksum + bytes((version,))).decode().lower() + ".onion"


@pytest.mark.parametrize(
    "uri",
    [f"ws://{ONION}", f"ws://{ONION}:8710", f"ws://{ONION}:8710/waggle", f"ws://{ONION.upper()}"],
)
def test_a_plaintext_uri_on_a_v3_onion_service_is_dialable(uri: str) -> None:
    assert check_waggle_uri(uri) == uri


@pytest.mark.parametrize(
    "host",
    [
        "hivestandhiddenservice.onion",  # Not the v3 shape at all.
        _onion(bytes(32), checksum_ok=False),  # A typo: the checksum does not hold.
        _onion(bytes(32), version=2),  # Not version 3.
        f"www.{ONION}",  # A subdomain of an onion address.
        ONION.removesuffix(".onion") + ".example",  # The right characters, the wrong name.
        "0" * 56 + ".onion",  # Outside the base32 alphabet.
    ],
)
def test_a_host_that_is_not_a_v3_onion_service_is_not_one(host: str) -> None:
    assert not is_onion_service_host(host)
    with pytest.raises(ValueError, match="plaintext endpoint"):
        check_waggle_uri(f"ws://{host}:8710")


def test_a_freshly_encoded_v3_address_is_one() -> None:
    assert is_onion_service_host(_onion(hashlib.sha256(b"a key").digest()))


def test_every_other_public_host_still_needs_tls() -> None:
    with pytest.raises(ValueError, match="a loopback host or a v3 onion service"):
        check_waggle_uri("ws://queen.example.org:8710")
