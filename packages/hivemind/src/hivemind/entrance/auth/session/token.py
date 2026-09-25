"""Mint session tokens, hash them for storage, and read one out of an Authorization header.

A session token at the Hive Entrance (the Hive's one HTTP door) is 32 random bytes from the CSPRNG,
handed to the device once, as unpadded base64url, and never stored: the Entrance keeps only its
SHA-256 (ADR-0041), so a copy of the Entrance tables holds nothing a client could present. Every
authenticated request carries it as ``Authorization: Bearer <token>``. Parsing is strict (the one
canonical spelling of exactly 32 bytes) so garbage is refused before any table is read, and so
one token can never be presented two ways.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.session``. Called
    by the session book (minting at login) and by request and socket authentication (hashing
    what a client presents). Calls into ``hivemind.entrance.auth.canonical`` only.

Key invariants:
    - ``token_hash`` accepts only a canonical 32-byte token; its result is 64 lowercase hex
      characters.
    - Nothing here logs or raises a token: errors name the rule, never the value.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the token.
    - hivemind.entrance.auth.session.book for where tokens are minted.
"""

from __future__ import annotations

import secrets

from hivemind.entrance.auth.canonical import b64url_decode, b64url_encode, sha256_hex

TOKEN_BYTES = 32  # ADR-0041: a random 256-bit bearer token.
TOKEN_CHARS = 43  # 32 bytes in unpadded base64url; anything else is refused unread.
_BEARER_SCHEME = "bearer"  # RFC 6750's scheme name, compared case-insensitively (RFC 9110).

__all__ = ["TOKEN_BYTES", "TOKEN_CHARS", "bearer_credential", "mint_token", "token_hash"]


def mint_token() -> str:
    """Mint a fresh session token: ``TOKEN_BYTES`` from the CSPRNG, as unpadded base64url.

    Returns:
        The token to hand the device once; store only ``token_hash`` of it.
    """
    return b64url_encode(secrets.token_bytes(TOKEN_BYTES))


def token_hash(token: str) -> str:
    """Return the SHA-256 the Entrance stores for ``token``.

    Args:
        token: A token as the client presented it.

    Returns:
        64 lowercase hex characters.

    Raises:
        ValueError: ``token`` is not the canonical spelling of exactly ``TOKEN_BYTES`` bytes.
    """
    # Length first: a cheap refusal for anything that cannot be a token, before decoding it.
    try:
        size = len(b64url_decode(token)) if len(token) == TOKEN_CHARS else 0
    except ValueError:
        size = 0
    if size != TOKEN_BYTES:
        raise ValueError("A session token is 32 bytes of unpadded base64url.")
    return sha256_hex(token.encode("ascii"))


def bearer_credential(authorization: str | None) -> str | None:
    """Read the credential out of an ``Authorization: Bearer <token>`` header value.

    Args:
        authorization: The header's value, or None when the request has none.

    Returns:
        The credential after the scheme, or None when the header is missing, uses another
        scheme, or carries no single credential.
    """
    if authorization is None:
        return None
    scheme, _, credential = authorization.partition(" ")
    # One scheme, one space, one credential: anything looser is not a bearer token.
    if scheme.lower() != _BEARER_SCHEME or not credential or " " in credential:
        return None
    return credential
