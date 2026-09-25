"""Tests for hivemind.entrance.auth.session.token: minting, hashing and reading session tokens.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/session/token.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.session.token for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.entrance.auth import b64url_decode, sha256_hex
from hivemind.entrance.auth.session import (
    TOKEN_BYTES,
    TOKEN_CHARS,
    bearer_credential,
    mint_token,
    token_hash,
)


def test_mint_token_draws_256_fresh_bits_every_time() -> None:
    tokens = {mint_token() for _ in range(8)}

    assert len(tokens) == 8
    assert all(len(token) == TOKEN_CHARS for token in tokens)
    assert all(len(b64url_decode(token)) == TOKEN_BYTES for token in tokens)


def test_token_hash_is_the_sha256_of_the_token_as_presented() -> None:
    token = mint_token()

    assert token_hash(token) == sha256_hex(token.encode("ascii"))


@pytest.mark.parametrize("token", ["", "short", "A" * 42 + "=", "A" * 44, "!" * 43])
def test_token_hash_refuses_anything_but_a_canonical_token(token: str) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        token_hash(token)


@pytest.mark.parametrize(
    ("header", "credential"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("Basic abc", None),
        ("Bearer", None),
        ("Bearer a b", None),
        (None, None),
    ],
)
def test_bearer_credential_reads_one_bearer_credential_only(
    header: str | None, credential: str | None
) -> None:
    assert bearer_credential(header) == credential
